"""Claude Code 세션 기록에서 '한도로 멈춘 세션'을 찾아낸다.

세션 기록은 `~/.claude/projects/<프로젝트>/<세션id>.jsonl` 에 한 줄에 하나씩 쌓인다.
이 모듈은 그 파일을 읽기만 한다 — 재개는 resume.py 가 맡는다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from afterlimit.config import Config
from afterlimit.limits import LIMIT_PATTERNS, LimitInfo, local_tz, parse_limit

__all__ = ["BlockedSession", "scan_blocked", "session_started_at"]


@dataclass(frozen=True)
class BlockedSession:
    session_id: str
    jsonl: Path
    cwd: str
    limit: LimitInfo
    #: 마지막 활동 시각 = 한도가 걸린 시점
    blocked_at: datetime
    #: 세션이 처음 만들어진 시각. 알 수 없으면 None
    started_at: datetime | None
    last_user: str = ""
    last_assistant: str = ""

    @property
    def project(self) -> str:
        return self.jsonl.parent.name


def _read_last_lines(path: Path, n: int = 80) -> list[str]:
    """파일 끝 n 줄. 큰 파일 전체를 메모리에 올리지 않는다."""
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            block = 8192
            data = b""
            while size > 0 and data.count(b"\n") <= n:
                step = min(block, size)
                size -= step
                f.seek(size)
                data = f.read(step) + data
    except OSError:
        return []
    return [ln for ln in data.decode("utf-8", "replace").splitlines() if ln.strip()]


def session_started_at(path: Path) -> datetime | None:
    """세션이 처음 만들어진 시각.

    파일의 첫 줄에 찍힌 timestamp 를 쓴다. `st_birthtime` 은 macOS 에만 있고,
    Linux 에서 mtime 으로 대신하면 재개할 때마다 갱신돼 나이 판단이 무의미해진다.
    첫 줄 timestamp 는 두 OS 에서 똑같이 동작하고 재개해도 변하지 않는다.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            first = f.readline()
    except OSError:
        return None
    try:
        raw = json.loads(first).get("timestamp")
    except (json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(raw, str):
        return None
    try:
        # Claude 는 "2026-07-01T22:29:17.151Z" 형식으로 쓴다
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _extract_last_api_error(lines: list[str]) -> tuple[str, bool]:
    """마지막 메시지가 API 에러(=한도)인지 본다.

    한도 메시지 뒤에 사람이나 다른 도구가 새 메시지를 붙였다면 이미 풀린 것이므로
    막힌 세션이 아니다. 그래서 '가장 마지막 메시지'만 본다 — 파일 전체를 훑으면
    과거의 한도 메시지나 한도를 언급하는 평범한 대화가 오탐이 된다.
    """
    for line in reversed(lines):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = obj.get("message") or {}
        role = msg.get("role")
        if role not in ("assistant", "user"):
            continue
        if role != "assistant" or not obj.get("isApiErrorMessage"):
            return "", False  # 한도 이후 활동이 있음
        content = msg.get("content")
        if isinstance(content, str):
            return content, True
        text = "".join(
            str(b.get("text", ""))
            for b in content or []
            if isinstance(b, dict) and b.get("type") == "text"
        )
        return text, True
    return "", False


def _extract_cwd_and_messages(lines: list[str]) -> tuple[str | None, str, str]:
    """작업 디렉터리와 마지막 user/assistant 발화.

    한도 에러 메시지도 형식상 assistant 메시지라서 그냥 훑으면 그게 '마지막 응답'이 된다.
    재개 폴백에서 "네 마지막 응답은 '한도 초과입니다'였다"고 알려주는 꼴이므로 걸러낸다.
    """
    cwd: str | None = None
    last_user = last_assistant = ""

    for line in lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj.get("cwd"), str) and obj["cwd"]:
            cwd = obj["cwd"]
        if obj.get("isApiErrorMessage"):
            continue  # 에러는 대화 내용이 아니다

        msg = obj.get("message") or {}
        role, content = msg.get("role"), msg.get("content")

        if role == "user":
            if isinstance(content, str):
                last_user = content
            elif isinstance(content, list):
                parts = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_result":
                        parts = []  # 도구 결과는 사람의 발화가 아니다
                        break
                    if block.get("type") == "text":
                        parts.append(str(block.get("text", "")))
                if parts:
                    last_user = "\n".join(parts)
        elif role == "assistant" and isinstance(content, list):
            parts = [
                str(b.get("text", ""))
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            if parts:
                last_assistant = "\n".join(parts)

    return cwd, last_user.strip(), last_assistant.strip()


def _is_human_turn(obj: dict) -> bool:
    """사람이 직접 친 입력인가. 도구 결과도 role=user 로 들어오므로 구분해야 한다."""
    msg = obj.get("message") or {}
    if msg.get("role") != "user":
        return False
    if obj.get("toolUseResult") is not None:
        return False  # 도구 결과
    content = msg.get("content")
    if isinstance(content, list):
        return not any(b.get("type") == "tool_result" for b in content if isinstance(b, dict))
    return True


def _cut_off_mid_action(obj: dict) -> bool:
    """마지막 레코드가 '결과가 안 돌아온 도구 호출'인가 = 하던 중에 끊겼다는 뜻."""
    msg = obj.get("message") or {}
    if msg.get("role") != "assistant":
        return False
    content = msg.get("content")
    if not isinstance(content, list):
        return False
    return any(b.get("type") == "tool_use" for b in content if isinstance(b, dict))


def _stalled_after_limit(
    lines: list[str], blocked_at: datetime, now: datetime, cfg: Config
) -> LimitInfo | None:
    """에이전트가 돌린 명령이 한도에 걸려, 하던 일이 끊긴 채 멈춘 세션.

    세션 **자신의** API 호출이 막힌 경우는 `_extract_last_api_error` 가 잡는다.
    여기서 잡는 건 다른 경우다 — 세션이 shell 로 실행한 명령이 한도에 걸린 것.
    화면에는 빨간 한도 메시지가 남지만 마지막 메시지는 정상 응답이라,
    예전에는 아무도 이어주지 않고 영원히 멈춰 있었다.

    조건 셋을 **모두** 만족해야 한다. 하나라도 빼면 오탐이 쏟아진다
    (2026-08-08 실측: ③ 없이는 최근 3일 세션 546개 중 279개가 걸렸다. 셋 다 쓰면 1개).
      ① 꼬리 N개 레코드 안에 한도 문구가 있다
      ② 그 뒤로 **사람이 입력한 적이 없다** (사람이 답했으면 이미 넘어간 것이다)
      ③ 마지막 레코드가 결과 없는 `tool_use` 다 — 하던 중에 끊긴 흔적

    그리고 사람이 지금 쓰고 있는 세션을 뺏지 않도록 일정 시간 멈춰 있어야 한다.
    """
    if not cfg.resume_stalled:
        return None
    if blocked_at > now - timedelta(minutes=cfg.stalled_idle_minutes):
        return None  # 아직 활동 중일 수 있다

    recs: list[dict] = []
    for line in lines[-cfg.stalled_tail_records :]:
        try:
            recs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not recs:
        return None

    last_limit = -1
    for i, obj in enumerate(recs):
        blob = json.dumps(obj, ensure_ascii=False).lower()
        if any(p in blob for p in LIMIT_PATTERNS):
            last_limit = i
    if last_limit < 0:
        return None  # ①

    if any(_is_human_turn(obj) for obj in recs[last_limit + 1 :]):
        return None  # ②
    if not _cut_off_mid_action(recs[-1]):
        return None  # ③

    # 해제 시각은 없다. 시간이 아니라 '아무도 안 이어줌'이 조건이므로 백오프가 속도를 정한다.
    return LimitInfo("stalled", None, "stalled after limit in a shell command")


def _inspect(jsonl: Path, cfg: Config, now: datetime) -> BlockedSession | None:
    """세션 파일 하나를 보고 막혔으면 BlockedSession, 아니면 None."""
    try:
        mtime_ts = jsonl.stat().st_mtime
    except OSError:
        return None

    tz = now.tzinfo or local_tz()
    blocked_at = datetime.fromtimestamp(mtime_ts, tz=tz)
    if blocked_at < now - timedelta(hours=cfg.active_within_hours):
        return None

    started = session_started_at(jsonl)
    if started and started < now - timedelta(days=cfg.max_session_age_days):
        return None  # 오래된 백로그 — 되살리지 않는다

    lines = _read_last_lines(jsonl)
    if not lines:
        return None

    text, is_error = _extract_last_api_error(lines)
    if is_error:
        limit = parse_limit(text, anchor=blocked_at, now=now)
    else:
        # 세션 자신이 막힌 건 아니지만, 돌리던 명령이 한도에 걸려 끊긴 채 멈췄을 수 있다.
        limit = _stalled_after_limit(lines, blocked_at, now, cfg)
    if limit is None:
        return None

    cwd, last_user, last_assistant = _extract_cwd_and_messages(lines)
    if not cwd:
        return None  # 어디서 이어갈지 모르면 재개할 수 없다

    return BlockedSession(
        session_id=jsonl.stem,
        jsonl=jsonl,
        cwd=cwd,
        limit=limit,
        blocked_at=blocked_at,
        started_at=started,
        last_user=last_user[:800],
        last_assistant=last_assistant[:800],
    )


def scan_blocked(cfg: Config, now: datetime | None = None) -> list[BlockedSession]:
    """한도로 멈춘 세션 목록. 최근에 막힌 것부터."""
    if now is None:
        now = datetime.now(local_tz())
    if not cfg.projects_dir.exists():
        return []

    found = [
        session
        for proj in sorted(cfg.projects_dir.iterdir())
        if proj.is_dir()
        for jsonl in sorted(proj.glob("*.jsonl"))
        if (session := _inspect(jsonl, cfg, now)) is not None
    ]
    return sorted(found, key=lambda s: s.blocked_at, reverse=True)
