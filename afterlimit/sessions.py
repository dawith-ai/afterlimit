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
from afterlimit.limits import LimitInfo, local_tz, parse_limit

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
    if not is_error:
        return None

    limit = parse_limit(text, anchor=blocked_at, now=now)
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
