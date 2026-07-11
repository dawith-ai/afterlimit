#!/usr/bin/env python3
"""scripts/resume_blocked_sessions.py — 봇 외부 안전망.

봇이 멈춰있어도 동작. launchd cron 으로 매 5분 실행.

== 동작 ==
1. ~/.claude/projects/*/*.jsonl 의 최근 12시간 mtime 세션 스캔
2. 각 jsonl 마지막 ~50 라인에서 claude 한도 메시지 매칭
3. reset 시각 추출 ("resets HH:MMam/pm")
4. 현재 시각 ≥ reset 시각이면 → 자동 재개:
   - 이전 user/assistant 컨텍스트 prepend
   - claude -p (continue 없이) 호출 → 응답 받음
5. 결과 → Discord 봇 채널로 webhook 보고 + state 파일에 기록

== 안전 ==
- standalone (봇 의존성 X)
- 같은 세션 cooldown 30분 (중복 fire 방지)
- 최대 2개 세션 처리 (cron 1회당)
- 모든 외부 호출 try/except (스크립트 자체 죽지 않게)

== 호출 ==
- launchd cron: */5 * * * *
- 또는 수동: python3 scripts/resume_blocked_sessions.py
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

KST = timezone(timedelta(hours=9))
HOME = Path.home()
PROJECTS_DIR = HOME / ".claude" / "projects"
BOT_DIR = HOME / "개발" / "openclaw-1"
STATE_FILE = BOT_DIR / "digest_memory" / "resume_safety_net.json"
LOG_FILE = Path("/tmp/openclaw_resume_safety.log")
LOCK_FILE = Path("/tmp/openclaw_resume_safety.lock")
DISCORD_API = "https://discord.com/api/v10"

# 일반 메신저 알림 (Discord/Telegram/Slack/임의 웹훅) — 같은 폴더 notify.py, 설정 없으면 no-op
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from notify import notify as _messenger_notify
except Exception:
    def _messenger_notify(_m: str) -> list:
        return []

# lsof 는 /usr/sbin 에 있는데 launchd PATH 엔 /usr/sbin 이 없음 → 절대경로로 호출.
# (2026-06-09 fix: live cwd 양보가 launchd 에서 FileNotFoundError 로 무효화되던 버그)
_LSOF = next((p for p in ("/usr/sbin/lsof", "/usr/bin/lsof", "/opt/homebrew/bin/lsof")
              if os.path.exists(p)), "lsof")

# 한도 메시지 패턴 (claude code 가 conversation 에 출력하는 모든 막힘 패턴)
LIMIT_PATTERNS = (
    "session limit",
    "usage limit",
    "5-hour limit",
    "weekly limit",
    "spend limit",  # 2026-06-29: "monthly spend limit" — reset 시각 없음(수동 raise 필요)
    "rate limited",  # 2026-06-04: API rate limit 추가
    "temporarily limiting",  # "Server is temporarily limiting requests"
    "rate limit",
)
RESET_RE = re.compile(
    # 형식 1: "resets 11pm" (시간만 — 오늘)
    # 형식 2: "resets Jun 6 at 11pm" (주간 한도 — 월/일 명시)
    r"resets?\s+(?:(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(\d{1,2})\s+at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)",
    re.IGNORECASE,
)
_MONTH_IDX = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
              "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}

ACTIVE_WITHIN_HOURS = 12
COOLDOWN_MIN = 3  # 15→3 (2026-06-04 fix): /지속 의도 — reset 시점 즉시 끊김없이 진행
# 안전망 패턴 skip 시 last_attempts 갱신 X — _check_skip_reasons 의 safety-pattern 분기는 return 만, 진짜 작업 cooldown 영향 0
MAX_RESUME_PER_CYCLE = 1  # 5→2→1 (2026-07-03): 10% cap — 사이클당 1개만.
RESUME_COOLDOWN_HOURS = 5  # 재개 후 이 시간(≈1 세션 window) 동안 재개 금지 → 세션당 1회 (10% cap)
                          # 20 동시 재개 = 5시간 계정 한도를 급속 소진 → 사용자 대화형 터미널이 더 자주 끊김.
INVOKE_TIMEOUT_SEC = 900  # 300→900 (15분): 진짜 작업 시간 확보
# 나이 제한 (2026-07-03): 세션 최초 생성(birthtime) 후 이 일수 넘으면 재개 안 함.
# mtime 은 재개할 때마다 갱신돼 오래된 세션이 계속 "신선"해지는 낭비 → birthtime 기준으로
# 진짜 오래된 죽은 백로그(7~30일 세션 다수)를 걸러 토큰 절약. birthtime 없으면 mtime fallback.
MAX_ORIGINAL_AGE_DAYS = 3
                          # 데이터: 성공 8건 평균 200s, max 295s → 5분 임계
                          # 11건 timeout 모두 진짜 작업 진행 중 강제 종료 (TOOL_RESULT 캡쳐)


def _log(msg: str) -> None:
    line = f"[{datetime.now(KST).isoformat()}] {msg}\n"
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    print(line.strip())


def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"last_attempts": {}, "resume_log": [], "stats": {"fire": 0, "ok": 0, "skip_cooldown": 0, "skip_blocked": 0}}


def _save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        _log(f"state save fail — {e}")


def _read_last_lines(path: Path, n: int = 80) -> list[str]:
    try:
        size = path.stat().st_size
        chunk = min(size, max(64 * 1024, n * 4 * 1024))
        with path.open("rb") as f:
            f.seek(max(0, size - chunk))
            tail = f.read().decode("utf-8", errors="replace")
        return tail.splitlines()[-n:]
    except Exception:
        return []


def _extract_cwd_user_assistant(lines: list[str]) -> tuple[str | None, str, str]:
    """jsonl 라인들에서 cwd + 마지막 user/assistant 메시지 추출."""
    cwd = None
    last_user = ""
    last_assistant = ""
    for line in lines:
        try:
            obj = json.loads(line)
        except Exception:
            continue
        c = obj.get("cwd")
        if isinstance(c, str) and c:
            cwd = c
        msg = obj.get("message") or {}
        role = msg.get("role")
        content = msg.get("content")
        if role == "user":
            if isinstance(content, str):
                last_user = content
            elif isinstance(content, list):
                parts = []
                stop = False
                for block in content:
                    if isinstance(block, dict):
                        t = block.get("type")
                        if t == "text":
                            parts.append(str(block.get("text", "")))
                        elif t == "tool_result":
                            stop = True
                            break
                if not stop and parts:
                    last_user = "\n".join(parts)
        elif role == "assistant":
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(str(block.get("text", "")))
                if parts:
                    last_assistant = "\n".join(parts)
    return cwd, last_user.strip(), last_assistant.strip()


def _extract_last_api_error_text(lines: list[str]) -> tuple[str, bool]:
    """가장 마지막 assistant 메시지가 isApiErrorMessage:true 인 경우 그 본문 text 반환.

    Returns (text, is_last_message_an_error).
    is_last_message_an_error == False 면 한도 메시지가 가장 최근 X = 이미 한도 풀려 사용자/안전망이
    새 작업 메시지를 추가한 상태 → blocked 처리 X.
    """
    # 마지막 비-meta assistant 또는 user 메시지 찾기 (텍스트 본문 있는 것)
    for line in reversed(lines):
        try:
            obj = json.loads(line)
        except Exception:
            continue
        msg = obj.get("message") or {}
        role = msg.get("role")
        if role not in ("assistant", "user"):
            continue
        # assistant + isApiErrorMessage = 진짜 한도 메시지
        if role == "assistant" and obj.get("isApiErrorMessage"):
            content = msg.get("content")
            text = ""
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text += str(block.get("text", ""))
            elif isinstance(content, str):
                text = content
            return text.lower(), True
        # 그 외 메시지 = 한도 풀린 후 새 활동 → False 즉시 반환
        return "", False
    return "", False


def _detect_limit_and_reset(lines: list[str], mtime_ts: float | None = None) -> tuple[bool, datetime | None, str]:
    """jsonl 끝부분에서 한도 메시지 + reset 시각 추출.

    핵심: jsonl 의 isApiErrorMessage:true 메타 필드 있는 가장 최근 assistant 메시지만 봄.
    그 메시지가 jsonl 의 가장 마지막 활동이어야 진짜 막힘 (한도 풀린 후 활동 있으면 blocked X).
    이전 버전은 tail_text 전체 검색 → 안전망 자기 보고의 "Rate limited" 글자가 false positive.

    mtime_ts (jsonl 파일 수정 시각) 가 주어지면 "시간만" 형식 ("resets 11pm") 의
    날짜 추론에 사용. 한도 발생 = mtime 시점 → reset 은 그 이후 가장 가까운 HH:MM.
    """
    tail_text, is_last_error = _extract_last_api_error_text(lines)
    if not is_last_error:
        return False, None, ""
    if not any(p in tail_text for p in LIMIT_PATTERNS):
        return False, None, ""
    # 서버 측 rate limit ("not your usage limit") 감지 시 → 가짜 reset 10분 후 부여
    # 이유: API rate limit 은 reset 시각 명시 X, 자동 재개가 가중하지 않게 10분 후 시도
    if "not your usage limit" in tail_text or "temporarily limiting" in tail_text or "rate limited" in tail_text:
        now = datetime.now(KST)
        return True, now + timedelta(minutes=10), "API rate limit (server-side, 10min cooldown)"
    # monthly spend limit — reset 시각 없음, 사용자가 claude.ai/settings/usage 에서 수동 raise 필요.
    # 30분 후 재시도 reset 부여 → 매 사이클 무의미 fire 차단 + raise 후 30분 내 자동 재개.
    if "spend limit" in tail_text:
        # ★ reset 을 '블록 시각(mtime)+30분'으로 앵커. (scan now 기준이면 매 스캔마다
        #    30분 미래로 밀려 영원히 재개 안 되는 버그) → 블록 30분 후 1회 재시도,
        #    한도 raise 됐으면 통과, 아직이면 재실패 후 skip_until 로 다시 30분 대기.
        anchor = datetime.fromtimestamp(mtime_ts, tz=KST) if mtime_ts else datetime.now(KST)
        return True, anchor + timedelta(minutes=30), "monthly spend limit (수동 raise 필요 · 30분 후 재시도)"
    # reset 시각 ("resets 5:50pm") — 사용자 토큰 한도
    m = RESET_RE.search(tail_text)
    if not m:
        return True, None, ""
    month_name = m.group(1)  # 옵션 — "Jun" 등 (주간 한도)
    day = m.group(2)          # 옵션 — "6" 등
    hour = int(m.group(3))
    minute = int(m.group(4) or 0)
    ampm = m.group(5).lower()
    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    now = datetime.now(KST)
    if month_name and day:
        # 주간 한도 형식 "resets Jun 6 at 11pm" — 명시 날짜 사용
        month_idx = _MONTH_IDX[month_name.lower()]
        year = now.year
        try:
            reset = datetime(year, month_idx, int(day), hour, minute, tzinfo=KST)
        except ValueError:
            # 잘못된 날짜 (예: 2월 30일) — 1시간 fallback
            reset = now + timedelta(hours=1)
        # 이미 한참 전 = 다음 해 (12월말 → 1월초 케이스)
        if reset < now - timedelta(days=180):
            reset = reset.replace(year=year + 1)
    else:
        # 형식 "resets 11pm" — 시간만
        # 핵심: 한도 발생 시각 (mtime) 기준으로 reset 추정 (자정 횡단 대응)
        # mtime 없으면 now 기준 fallback
        anchor = datetime.fromtimestamp(mtime_ts, tz=KST) if mtime_ts else now
        reset = anchor.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if reset < anchor:
            # 한도 발생 시점보다 이전 = 다음날 같은 시각
            reset += timedelta(days=1)
        # 이제 reset 은 한도 발생 시점 이후 가장 가까운 HH:MM
        # 추가 안전망: reset 이 이미 한참 (12h) 지났으면 → mtime 기반 추정 실패
        # → now 기준 다음 사이클로
        if reset <= now - timedelta(hours=12):
            reset = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if reset <= now - timedelta(hours=6):
                reset += timedelta(days=1)
    return True, reset, m.group(0)


def _scan_active_blocked() -> list[dict]:
    """최근 N시간 활성 + 한도 메시지 있는 세션."""
    if not PROJECTS_DIR.exists():
        return []
    cutoff = time.time() - ACTIVE_WITHIN_HOURS * 3600
    blocked = []
    for proj_dir in PROJECTS_DIR.iterdir():
        if not proj_dir.is_dir():
            continue
        for jsonl in proj_dir.glob("*.jsonl"):
            try:
                mtime = jsonl.stat().st_mtime
            except Exception:
                continue
            if mtime < cutoff:
                continue
            # 나이 제한: 세션 최초 생성 후 MAX_ORIGINAL_AGE_DAYS 지난 죽은 백로그는 재개 안 함.
            # birthtime(생성시각)은 재개해도 안 바뀌어 진짜 나이를 반영 → 토큰 절약.
            try:
                st = jsonl.stat()
                birth = getattr(st, "st_birthtime", st.st_mtime)
                if (time.time() - birth) > MAX_ORIGINAL_AGE_DAYS * 86400:
                    continue
            except OSError:
                continue
            lines = _read_last_lines(jsonl, n=80)
            if not lines:
                continue
            is_blocked, reset_at, raw = _detect_limit_and_reset(lines, mtime_ts=mtime)
            if not is_blocked:
                continue
            cwd, last_user, last_assistant = _extract_cwd_user_assistant(lines)
            if not cwd:
                continue
            blocked.append({
                "cwd": cwd,
                "jsonl": str(jsonl),
                "mtime": mtime,
                "reset_at": reset_at.isoformat() if reset_at else None,
                "limit_raw": raw,
                "last_user": last_user[:800],
                "last_assistant": last_assistant[:800],
            })
    return blocked


_RESUME_PROMPT = (
    "방금 토큰 한도가 풀린 시점입니다. 진행 중이던 작업을 자율적으로 이어서 진행해주세요. "
    "추가 확인 질문 없이 가능한 부분은 즉시 실행. "
    "★ 1순위: 이 프로젝트의 최신 핸드오프/작업일지를 먼저 읽고 거기 적힌 '다음 작업(미완료 TODO)'을 실제로 이어가세요 "
    "(존재하는 것: HANDOFF*.md · 작업일지.md · SESSION_COORDINATION.md · OVERNIGHT_WATCH.md · tmp/*handoff*.md 순). "
    "★ '점검 결과 손댈 것 없음/신규 회귀 0'으로 끝내지 마세요 — 핸드오프에 미완료 항목이 있으면 그 중 하나를 반드시 실제로 진행합니다(정말 0건일 때만 점검 보고). "
    "★ 15분 내 끝낼 단위로 진행하되, 코드·문서 변경은 즉시 git 커밋해서 다음 세션이 끊김 없이 이어받게 하고, 끝낸 일과 '다음 작업'을 핸드오프 파일에 갱신하세요. "
    "위험작업(rm -rf · git push -f · git reset --hard · DB DELETE/DROP/UPDATE · kill -9 · .env 수정)은 사용자 확인 전까지 금지. "
    "완료되면 무엇을 끝냈고 다음 작업이 무엇인지 한국어로 한 줄 보고."
)


def _run_claude(cmd: list, cwd: str) -> tuple[int, str, str, float]:
    t0 = time.time()
    try:
        r = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=INVOKE_TIMEOUT_SEC,
        )
        return r.returncode, r.stdout or "", r.stderr or "", time.time() - t0
    except subprocess.TimeoutExpired:
        return -2, "", "timeout", time.time() - t0
    except Exception as e:
        return -1, "", f"{type(e).__name__}: {e}", time.time() - t0


def _resume_one(cwd: str, last_user: str, last_assistant: str, project_name: str,
                session_id: str = "") -> tuple[int, str, str, float]:
    """실제 세션을 --resume 으로 전체 맥락(진행 중 TodoList·작업상태) 그대로 이어감.
    구조적 실패(세션 못 찾음 등) 시에만 fresh -p (마지막 컨텍스트 800자 prepend) 폴백."""
    base = ["--output-format", "text", "--max-turns", "60", "--dangerously-skip-permissions"]
    # 1순위: 실제 세션 resume — 800자 요약이 아니라 전체 맥락으로 진짜 이어감
    if session_id:
        rc, out, err, elapsed = _run_claude(
            ["claude", "--resume", session_id, "-p", _RESUME_PROMPT] + base, cwd
        )
        combined = (out + "\n" + err).lower()
        # 한도 메시지·정상 응답은 그대로 반환. rc!=0 + 빈 출력 + 한도아님 = 구조적 실패만 폴백
        structural_fail = (rc != 0 and not out.strip()
                           and not any(p in combined for p in LIMIT_PATTERNS))
        if not structural_fail:
            return rc, out, err, elapsed
        _log(f"     --resume 실패 → fresh 폴백 ({project_name}): {err.strip()[:80]}")
    # 2순위(폴백): 기존 fresh -p + 마지막 컨텍스트 prepend
    context = ""
    if last_user or last_assistant:
        context = (
            "[이전 대화의 마지막 컨텍스트]\n"
            f"마지막 사용자 요청: {last_user[:800]}\n\n"
            f"마지막 어시스턴트 응답: {last_assistant[:800]}\n\n"
            "[현재 지시]\n"
        )
    return _run_claude(["claude", "-p", context + _RESUME_PROMPT] + base, cwd)


ADMIN_CMD_PATH = "/tmp/openclaw_admin_cmd.txt"
SAFETY_REPORT_PATH = "/tmp/openclaw_safety_report.txt"
RESUME_LOCK_DIR = Path("/tmp/openclaw_resume_locks")
LOCK_STALE_SEC = 600  # 10분 이상 된 lock 은 stale (자동 해제)


def _acquire_resume_lock(key: str) -> bool:
    """jsonl 별 lock — 같은 conversation 의 봇+cron 동시 fire 만 차단.

    이전: cwd 단위 → 같은 레포의 여러 conversation (jsonl) 가 1개씩만 순차 처리 → 60개 = 5시간.
    이번: jsonl path 단위 → 같은 레포 안의 여러 conversation 병렬 처리 가능.
    각 cycle 의 동시 fire 수는 MAX_RESUME_PER_CYCLE 로 여전히 제한.

    반환: True = 락 획득 OK · False = 같은 jsonl 이 이미 진행 중
    """
    try:
        RESUME_LOCK_DIR.mkdir(parents=True, exist_ok=True)
        import hashlib
        key_hash = hashlib.md5(key.encode("utf-8")).hexdigest()[:16]
        lock_path = RESUME_LOCK_DIR / f"{key_hash}.lock"
        if lock_path.exists():
            age = time.time() - lock_path.stat().st_mtime
            if age < LOCK_STALE_SEC:
                return False
            lock_path.unlink(missing_ok=True)
        lock_path.write_text(f"pid={os.getpid()} key={key}", encoding="utf-8")
        return True
    except Exception:
        return True


def _release_resume_lock(key: str) -> None:
    try:
        import hashlib
        key_hash = hashlib.md5(key.encode("utf-8")).hexdigest()[:16]
        lock_path = RESUME_LOCK_DIR / f"{key_hash}.lock"
        lock_path.unlink(missing_ok=True)
    except Exception:
        pass


def _notify(content: str, important: bool = False) -> bool:
    """다중 폴백 알림:
    1. /tmp/openclaw_safety_report.txt 에 항상 기록 (봇이 읽어 보고 가능)
    2. 봇 admin_cmd 통해 디스코드 (important=True 만 — 실패/사고)
    3. macOS 알림 센터 (important=True 만)

    important=False(기본): 파일 기록만. 일상적 성공 재개는 디스코드 알림 X.
    (2026-06-13: 알림 과다 정리 — 매 재개마다 작업방에 !세션 발사하던 노이즈 제거)
    """
    # 1. 영구 기록 (항상 — 상세는 파일/스레드에 남고, 채널엔 중요한 것만)
    try:
        with open(SAFETY_REPORT_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now(KST).isoformat()}]\n{content}\n\n")
    except Exception:
        pass
    # 일반 메신저(Discord/Telegram/Slack/임의 웹훅) 전송 — notify.json 설정된 경우만, 실패 무시
    try:
        _messenger_notify(content)
    except Exception:
        pass
    # 중요하지 않으면 (로컬 봇/macOS 알림은) 여기서 끝
    if not important:
        return True
    # 2. 봇 admin_cmd 사용 — 봇이 살아있으면 작업방에 발사
    try:
        # 봇이 처리할 명령: 직접 채널 send 가 아니라 봇 명령으로 변환
        # 봇이 _admin_cmd_watcher 로 픽업해서 실행
        # 안전: 너무 자주 발사 X — 마지막 admin send 후 60초 cooldown
        with open(ADMIN_CMD_PATH, "w", encoding="utf-8") as f:
            f.write(f"!모니터")  # 단순 트리거 — 봇이 자체로 채널에 send (구 !세션, 이름 변경됨)
        # 별도: 작업방에 직접 send 하려면 봇이 명령으로 처리해야 하니 단순 trigger
    except Exception:
        pass
    # 3. macOS 알림 (즉시 사용자 시야)
    try:
        # AppleScript 안전 escape: \ → /, " → ', 줄바꿈 → |, 200자 cap
        # 추가로 control chars 제거
        oneline = (
            content.replace("\\", "/")
            .replace('"', "'")
            .replace("\n", " | ")
            .replace("\r", " ")
            .replace("\t", " ")
        )
        # printable 만 (제어 문자 거부)
        oneline = "".join(c for c in oneline if c.isprintable() or c == " ")[:200]
        subprocess.Popen(
            ["osascript", "-e",
             f'display notification "{oneline}" with title "openclaw 안전망"'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
    return True


def _is_safety_check_pattern(last_user: str) -> bool:
    """last_user 가 안전망 점검 명령이면 True (재개해도 안전망만 반복).

    원인 (2026-06-03): 사용자 외부 자동화가 30분마다 안전망 점검 명령 보냄 →
    그 conversation 들의 last_user = "안전망 점검 명령" → 자동 재개 시 안전망 만 반복.
    진짜 작업 (news-auto 발행 / 봇 디벨롭 등) 은 안 이어짐.
    → 안전망 패턴 감지 시 skip → 진짜 작업 conversation 만 자동 재개.
    """
    if not last_user:
        return False
    # 안전망이 스스로 재개한 세션의 재귀 — _resume_one 의 context-prepend("[이전 대화의 마지막 컨텍스트]")가
    # 2회 이상 중첩 = "재개의 재개" 좀비. (2026-06-08 fix: 막힌 세션 1948개 중 1940개가 이 재귀 좀비로
    # 매 사이클 무의미 재개 → 토큰/프로세스 대량 소모. 1차 재개(1회)는 진짜 작업 연장이므로 보존.)
    if last_user.count("[이전 대화의 마지막 컨텍스트]") >= 2:
        return True
    lower = last_user.lower()
    safety_markers = [
        "openclaw 자동 안전망 점검",
        "resume_blocked_sessions.py",
        "스캔 — 막힌 세션",
        "▶️ fire",
        "점검만",
        "자동 안전망 시스템을 다음 순서",
    ]
    return any(m in last_user for m in safety_markers) or any(m in lower for m in [s.lower() for s in safety_markers])


def _check_skip_reasons(state: dict, session: dict, now: datetime) -> Optional[str]:
    """skip 사유 반환 (None 이면 fire 가능). state.stats 갱신."""
    cwd = session["cwd"]
    jsonl = session["jsonl"]  # cooldown 도 jsonl 단위 — 같은 cwd 다른 conversation 병렬 처리
    project = os.path.basename(cwd.rstrip("/"))
    # 0) 안전망 점검 패턴이면 skip (진짜 작업 X)
    last_user = session.get("last_user", "")
    if _is_safety_check_pattern(last_user):
        state["stats"].setdefault("skip_safety_pattern", 0)
        state["stats"]["skip_safety_pattern"] += 1
        return f"safety-pattern — {project} (사용자 안전망 점검 자동화)"
    # 0.5) server skip_until — fail 시 추출한 새 reset 시각 기준 jsonl 별 skip
    # (서버 측 진짜 한도 — jsonl 메타엔 없는 새 한도. 96% 무의미 fire 차단)
    skip_until = state.get("skip_until", {}).get(jsonl)
    if skip_until:
        try:
            su = datetime.fromisoformat(skip_until)
            if now < su:
                state["stats"]["skip_blocked"] += 1
                return f"server-blocked — {project} (until {su.strftime('%H:%M')})"
            # 도달 → 제거
            state["skip_until"].pop(jsonl, None)
        except Exception:
            state.get("skip_until", {}).pop(jsonl, None)
    # 1) cooldown — jsonl 단위 (같은 conversation 만 3분 cooldown, 같은 cwd 다른 conversation 즉시 가능)
    last_attempt = state["last_attempts"].get(jsonl)
    if last_attempt:
        try:
            la = datetime.fromisoformat(last_attempt)
            if (now - la).total_seconds() < COOLDOWN_MIN * 60:
                state["stats"]["skip_cooldown"] += 1
                return f"cooldown — {project}"
        except Exception:
            pass
    # 2) reset 시각 미도달
    reset_at = session.get("reset_at")
    if reset_at:
        try:
            ra = datetime.fromisoformat(reset_at)
            if now < ra:
                state["stats"]["skip_blocked"] += 1
                return f"blocked — {project} (reset {ra.strftime('%H:%M')})"
        except Exception:
            pass
    return None


def _process_session(state: dict, session: dict, now: datetime, skips: Optional[dict] = None) -> bool:
    """한 세션 자동 재개. 반환: True=fire, False=skip.

    skips: 전달되면 skip 사유를 개별 로그 대신 사유별로 집계 (로그 스팸 방지).
    """
    skip_reason = _check_skip_reasons(state, session, now)
    if skip_reason:
        if skips is not None:
            skips[skip_reason] = skips.get(skip_reason, 0) + 1
        else:
            _log(f"  skip {skip_reason}")
        return False
    cwd = session["cwd"]
    jsonl = session["jsonl"]  # jsonl 단위 lock key — 같은 cwd 의 다른 conversation 병렬 fire 허용
    project = os.path.basename(cwd.rstrip("/"))
    # 동시 fire 차단 — 같은 jsonl 만 (봇 watch_loop + cron 동일 conversation 중복 차단)
    if not _acquire_resume_lock(jsonl):
        lock_reason = f"lock — {project} (같은 conversation 진행 중)"
        if skips is not None:
            skips[lock_reason] = skips.get(lock_reason, 0) + 1
        else:
            _log(f"  skip {lock_reason}")
        state["stats"]["skip_cooldown"] += 1
        return False
    _log(f"  ▶️ fire — {project} (cwd={cwd})")
    state["last_attempts"][jsonl] = now.isoformat()  # jsonl 단위 cooldown 기록
    state["stats"]["fire"] += 1
    try:
        rc, out, err, elapsed = _resume_one(
            cwd, session["last_user"], session["last_assistant"], project,
            os.path.splitext(os.path.basename(jsonl))[0],  # jsonl 파일명 = 세션ID
        )
    finally:
        _release_resume_lock(jsonl)
    ok = rc == 0 and bool(out.strip())
    # 응답이 또 한도 메시지면 fail + 새 reset 시각 추출해서 jsonl별 skip_until 등록
    # (이전: fail 후 같은 jsonl 다음 cycle 또 fire → 무의미 fail 폭주. 96% fail 발견)
    combined = (out + "\n" + err).lower()
    if any(p in combined for p in LIMIT_PATTERNS):
        ok = False
        new_reset_m = RESET_RE.search(combined)
        if new_reset_m:
            try:
                m_name = new_reset_m.group(1)
                m_day = new_reset_m.group(2)
                m_hour = int(new_reset_m.group(3))
                m_min = int(new_reset_m.group(4) or 0)
                m_ampm = new_reset_m.group(5).lower()
                if m_ampm == "pm" and m_hour != 12:
                    m_hour += 12
                elif m_ampm == "am" and m_hour == 12:
                    m_hour = 0
                if m_name and m_day:
                    m_idx = _MONTH_IDX[m_name.lower()]
                    new_reset = datetime(now.year, m_idx, int(m_day), m_hour, m_min, tzinfo=KST)
                    if new_reset < now - timedelta(days=180):
                        new_reset = new_reset.replace(year=now.year + 1)
                else:
                    new_reset = now.replace(hour=m_hour, minute=m_min, second=0, microsecond=0)
                    if new_reset < now:
                        new_reset += timedelta(days=1)
                state.setdefault("skip_until", {})[jsonl] = new_reset.isoformat()
                _log(f"     ⏸ skip_until {new_reset.strftime('%m-%d %H:%M')} — {project} (서버 새 한도, 다음 cycle 무의미 fire 차단)")
            except Exception as e:
                _log(f"     skip_until 파싱 실패: {e}")
        elif "spend limit" in combined:
            # reset 시각 없는 spend limit — 30분 후 다시 시도 (한도 올렸으면 그때 통과)
            state.setdefault("skip_until", {})[jsonl] = (now + timedelta(minutes=30)).isoformat()
            _log(f"     ⏸ skip_until +30m — {project} (spend limit · 수동 raise 대기)")
    first_line = (out.strip().splitlines() or ["(empty)"])[0][:200]
    state["resume_log"].append({
        "at": now.isoformat(),
        "cwd": cwd,
        "project": project,
        "rc": rc,
        "ok": ok,
        "elapsed": round(elapsed, 1),
        "summary": first_line,
    })
    if len(state["resume_log"]) > 50:
        state["resume_log"] = state["resume_log"][-50:]
    if ok:
        state["stats"]["ok"] += 1
        _log(f"     ✅ ok ({elapsed:.1f}s): {first_line}")
        _notify(
            f"openclaw 안전망 — {project} 끊긴 세션 이어서 진행 완료 ({elapsed:.0f}초). {first_line[:200]}"
        )
    else:
        _log(f"     ⚠️ fail rc={rc} ({elapsed:.1f}s): {first_line}")
        _notify(
            f"openclaw 안전망 — {project} 이어서 진행 실패 (rc={rc}). 사유: {first_line[:200]}",
            important=True,
        )
    return True


def _active_interactive_cwds() -> set:
    """현재 살아있는 '대화형' claude 세션(claude -p / 안전망 아님)의 cwd 집합.

    사용자가 그 디렉터리에서 직접 작업 중이면 안전망이 같은 작업을 또 재개해봐야
    중복일 뿐이고, 하나의 계정 한도 quota 를 두고 사용자 live 세션과 경쟁해서
    오히려 보이는 터미널을 더 자주 끊기게 만든다. → 그런 cwd 는 안전망이 양보(skip).
    (2026-06-08 fix: "내 보이는 작업이 계속 끊긴다" 대응 — 안전망의 quota 절도 차단.)

    탐지 실패 시 빈 set 반환(fail-open) — 기존처럼 전부 재개, 안전.
    """
    cwds: set = set()
    try:
        pids = subprocess.run(
            ["pgrep", "-f", "claude"], capture_output=True, text=True, timeout=10
        ).stdout.split()
        for pid in pids:
            try:
                cmd = subprocess.run(
                    ["ps", "-o", "command=", "-p", pid],
                    capture_output=True, text=True, timeout=5,
                ).stdout
                # 대화형 claude 만 — headless(claude -p)·안전망 스크립트는 제외
                if "claude -p" in cmd or "resume_blocked" in cmd:
                    continue
                if "claude" not in cmd.split("\n")[0]:
                    continue
                lsof_out = subprocess.run(
                    [_LSOF, "-a", "-p", pid, "-d", "cwd", "-Fn"],
                    capture_output=True, text=True, timeout=5,
                ).stdout
                for line in lsof_out.splitlines():
                    if line.startswith("n"):
                        # macOS 한글 경로 NFC/NFD 불일치 방지 — NFC 정규화 후 저장
                        cwds.add(unicodedata.normalize("NFC", line[1:].rstrip("/")))
            except Exception:
                continue
    except Exception:
        pass
    return cwds


def _weekly_util() -> float:
    """주간 Claude 사용량 % (claude_usage_state.json). 못 읽으면 0."""
    try:
        p = BOT_DIR / "digest_memory" / "claude_usage_state.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        return float(d.get("seven_day_utilization") or 0)
    except Exception:
        return 0.0


WEEKLY_YIELD_THRESHOLD = 95.0  # 주간 이 % 이상이면 안전망 양보 (quota 보존, reset 까지 대기)


def _session_util() -> float:
    """5시간 세션 사용량 % (claude_usage_state.json 의 five_hour_utilization). 못 읽으면 0.
    홈페이지 '현재 세션 N% 사용됨 · N시간 후 재설정' 과 동일 값."""
    try:
        p = BOT_DIR / "digest_memory" / "claude_usage_state.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        return float(d.get("five_hour_utilization") or 0)
    except Exception:
        return 0.0


# ★ 2026-07-03: 5시간 세션한도 페이싱. 세션이 이 % 이상 차면 리셋까지 양보 →
#   세션예산을 순식간에 안 태우고, 나머지 %는 사용자 대화형 작업 몫으로 남긴다.
#   (홈페이지 '현재 세션 N% · N시간 후 재설정' 계산에 맞춘 알뜰 페이싱)
SESSION_YIELD_THRESHOLD = 10.0  # 5시간 세션 이 % 넘으면 양보 (10% cap — 사용자 90% 보존)


def _session_reset_at() -> Optional[datetime]:
    """5시간 세션 리셋 시각 (five_hour_resets_at, UTC ISO) → KST. 못 읽으면 None.
    홈페이지 'N시간 후 재설정' 과 동일. 리셋 시각을 '관통'해 정확히 그때 재개하기 위함."""
    try:
        p = BOT_DIR / "digest_memory" / "claude_usage_state.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        return datetime.fromisoformat(d["five_hour_resets_at"]).astimezone(KST)
    except Exception:
        return None


def _refresh_claude_oauth_token_file() -> None:
    """★ 2026-06-22: news-auto cron(데몬, 키체인 접근불가)이 claude_code를 쓰도록,
    사용자 컨텍스트(LaunchAgent)인 이 안전망이 매 사이클 키체인의 claude OAuth accessToken을
    파일로 갱신해 둔다. brain._call_claude_code 가 키체인 실패 시 이 파일을 읽어 주입한다.
    토큰은 ~수시간마다 만료되나 사용자 대화형 사용으로 키체인이 갱신되므로 항상 최신을 복사."""
    try:
        import subprocess as _sp, json as _json
        r = _sp.run(["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                    capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            tok = _json.loads(r.stdout.strip()).get("claudeAiOauth", {}).get("accessToken", "")
            if tok and tok.startswith("sk-ant-oat"):
                p = Path.home() / ".claude" / "cron_oauth_token"
                p.write_text(tok, encoding="utf-8")
                try:
                    p.chmod(0o600)
                except OSError:
                    pass
    except Exception:
        pass  # 실패해도 안전망 본업에 영향 없음


def main() -> int:
    """안전망 cycle — 막힌 세션 스캔 + 사이클당 최대 MAX_RESUME 개 재개."""
    _refresh_claude_oauth_token_file()
    state = _load_state()
    now = datetime.now(KST)
    # ★ 주간 한도 거의 소진 시 안전망 양보 — 자동재개가 남은 quota 를 더 태우지 않게.
    #   (foreground 작업용으로 남겨둠. reset 후 자동 재가동.)
    wk = _weekly_util()
    if wk >= WEEKLY_YIELD_THRESHOLD:
        _log(f"⏸ 주간 사용량 {wk:.0f}% — 안전망 양보 (quota 보존, reset 까지 자동재개 중단)")
        return 0
    # ★ 10% cap: 재개 후 5시간(≈1 세션 window) 쿨다운 → 세션당 1회만 재개(저비용).
    #   (2026-07-04: reset시각 키 가드가 리셋 직후 값 shift로 2회 통과 → 시간 쿨다운으로 견고화)
    sess = _session_util()
    sra = _session_reset_at()
    lra = state.get("last_resume_at")
    if lra:
        try:
            if (now - datetime.fromisoformat(lra)).total_seconds() < RESUME_COOLDOWN_HOURS * 3600:
                _log("⏸ 최근 재개 후 쿨다운 중 — 다음 세션까지 대기 (10% cap · 세션당 1회)")
                return 0
        except Exception:
            pass
    # 세션 페이싱: 세션이 임계% 차고 '아직 리셋 전'이면 양보 → 리셋 시각을 관통해 그때 재개.
    if sess >= SESSION_YIELD_THRESHOLD:
        if sra is None or datetime.now(KST) < sra:
            when = f" · {sra:%H:%M} 리셋까지" if sra else ""
            _log(f"⏸ 5시간 세션 {sess:.0f}% — 안전망 양보{when}, 사용자 몫 보존")
            return 0
    raw = _scan_active_blocked()
    # 안전망 자기 세션 (self-loop) 은 스캔 단계에서 제외 — 카운트 부풀림/로그 노이즈 방지.
    # _check_skip_reasons 에서 어차피 skip 되지만, 매 사이클 누적되어 backlog 가
    # 늘어나는 것처럼 보이는 착시(2026-06-03: 38→41)를 없앤다.
    blocked = [s for s in raw if not _is_safety_check_pattern(s.get("last_user", ""))]
    skipped_safety = len(raw) - len(blocked)
    # 주간 한도 보존 트래커 — reset 이 12h+ 미래(주간/다일)인 세션은 mtime 12h 창에서
    # 곧 빠지므로, state 에 따로 저장했다가 reset 도달 시 재개. (2026-06-09: 주간 리셋 시점 맞춤)
    # 5시간 한도 경로는 그대로 — 여기는 후보를 '추가만' 하므로 기존 동작 불변.
    pending = state.setdefault("pending_resets", {})
    horizon = now + timedelta(hours=ACTIVE_WITHIN_HOURS)
    for s in blocked:
        ra = s.get("reset_at")
        if not ra:
            continue
        try:
            if datetime.fromisoformat(ra) > horizon:  # 12h 넘게 미래 = 주간/다일 한도
                pending[s["jsonl"]] = {
                    "reset_at": ra, "cwd": s["cwd"],
                    "last_user": s["last_user"], "last_assistant": s["last_assistant"],
                }
        except Exception:
            pass
    seen = {s["jsonl"] for s in blocked}
    for jsonl, info in list(pending.items()):
        try:
            ra = datetime.fromisoformat(info["reset_at"])
        except Exception:
            pending.pop(jsonl, None)
            continue
        if ra > now + timedelta(days=8) or now >= ra + timedelta(hours=24):
            pending.pop(jsonl, None)  # 비정상 미래 or reset+24h 경과 → 정리
            continue
        if now >= ra and jsonl not in seen:  # reset 도달 + 일반 스캔엔 없음 → 부활
            blocked.append({
                "cwd": info["cwd"], "jsonl": jsonl, "mtime": 0.0,
                "reset_at": info["reset_at"], "limit_raw": "pending-weekly",
                "last_user": info["last_user"], "last_assistant": info["last_assistant"],
            })
            _log(f"  ⏰ 주간 reset 도달 — pending 부활: {os.path.basename(info['cwd'].rstrip('/'))}")
    # 사용자가 직접 작업 중인 cwd 는 양보 — 안전망이 끼어들어 quota 경쟁/중복하지 않게.
    # ★ 단, reset 이 이미 지난 '진짜 막힌' 세션은 양보 안 함:
    #   사용자가 보는 건 live 세션이지 막힌 세션이 아니므로, 같은 cwd 에 다른 live 세션이
    #   있어도 /지속 이 끊기지 않게 재개해야 함 (같은 레포 옆 세션 stranding 버그 fix).
    live_cwds = _active_interactive_cwds()
    if live_cwds:
        _now = datetime.now(KST)
        before = len(blocked)
        kept = []
        for s in blocked:
            cwd_live = unicodedata.normalize("NFC", s["cwd"].rstrip("/")) in live_cwds
            reset_passed = False
            if s.get("reset_at"):
                try:
                    reset_passed = datetime.fromisoformat(s["reset_at"]) <= _now
                except Exception:
                    reset_passed = False
            if cwd_live and not reset_passed:
                continue  # 아직 reset 전 + live cwd → 양보 (quota 경쟁 방지)
            kept.append(s)
        skipped_live = before - len(kept)
        blocked = kept
    else:
        skipped_live = 0
    if not blocked:
        extra = f", live세션 cwd {skipped_live} 양보" if skipped_live else ""
        _log(f"스캔 — 막힌 세션 0 (안전망 자기세션 {skipped_safety} 제외{extra})")
        return 0
    live_suffix = f" · live세션 cwd {skipped_live} 양보" if skipped_live else ""
    suffix = f" (안전망 자기세션 {skipped_safety} 제외{live_suffix})" if (skipped_safety or skipped_live) else ""
    _log(f"스캔 — 막힌 세션 {len(blocked)} 개{suffix}")
    # monthly spend limit (수동 raise 필요 · 자동재개 불가) 세션 — 1시간당 1회 중요 알림.
    spend_blocked = [s for s in blocked if "spend limit" in (s.get("limit_raw") or "")]
    if spend_blocked:
        ln = state.get("spend_limit_notified_at")
        ok_notify = True
        if ln:
            try:
                ok_notify = (now - datetime.fromisoformat(ln)).total_seconds() > 3600
            except Exception:
                ok_notify = True
        if ok_notify:
            projs = ", ".join(sorted({os.path.basename(s["cwd"].rstrip("/")) for s in spend_blocked}))
            _notify(
                f"⚠️ monthly spend limit — {len(spend_blocked)}개 세션 막힘 ({projs}). "
                f"claude.ai/settings/usage 에서 한도 올리면 30분 내 자동 재개됩니다.",
                important=True,
            )
            state["spend_limit_notified_at"] = now.isoformat()
    resumed = 0
    skips: dict = {}
    # 레포별 공정 분배 (round-robin) — 한 레포(news-auto 등) backlog 가 수천 개여도
    # 다른 레포(openclaw / openclaw-1 등)가 매 사이클 앞쪽 보장 슬롯을 받게 interleave.
    # (2026-06-08 fix: news-auto 1967개가 MAX_RESUME 20 슬롯을 독점해 타 레포 기아 발생 → 공정화)
    by_repo: dict = {}
    for s in blocked:
        # ★ 2026-06-21 fix: basename 으로 묶으면 서로 다른 경로의 동명 레포(운영본/작업본)가
        #   둘 다 같은 이름으로 같은 큐에 섞여, 세션 많은 쪽이 다른 쪽을 pop(0) 뒤로 계속 밀어냄.
        #   → 전체 cwd 를 키로 써서 두 경로를 별도 큐로 분리, 양쪽 다 매 사이클 슬롯 보장.
        repo = s["cwd"].rstrip("/")
        by_repo.setdefault(repo, []).append(s)
    # ★ 2026-07-03 알뜰: 프로젝트(cwd)당 '최신 세션 1개'만 재개 대상으로 축소.
    #   같은 프로젝트의 오래된 중복 좀비 세션(예: news-auto 81개)을 개별 재개하는 건
    #   순수 토큰 낭비 — 최신 세션이 그 프로젝트의 최신 핸드오프/상태를 담고 있어,
    #   1개만 이어가면 프로젝트 작업이 그대로 계속된다. (재개대상 ~193 → 프로젝트수 ~8, ~96% 절감)
    _dedup_before = sum(len(v) for v in by_repo.values())
    for repo in list(by_repo):
        by_repo[repo] = [max(by_repo[repo], key=lambda x: x.get("mtime", 0.0))]
    _dedup_after = len(by_repo)
    if _dedup_before > _dedup_after:
        _log(f"  🪙 프로젝트당 최신1개로 중복제거: {_dedup_before} → {_dedup_after} (토큰절약)")
    ordered: list = []
    # ★ 환경변수 CLAUDE_AUTO_LIVE_PRIORITY 로 지정한 cwd 를 라운드로빈 맨 앞 큐로 배치해
    #   매 사이클 첫 슬롯 보장 (핵심 실작업 세션이 토큰리셋 후 안 끊기게). 미설정이면 동등 처리.
    _LIVE_PRIORITY = os.environ.get("CLAUDE_AUTO_LIVE_PRIORITY", "").rstrip("/")
    queues = sorted(
        by_repo.values(),
        key=lambda q: 0 if (q and q[0]["cwd"].rstrip("/") == _LIVE_PRIORITY) else 1,
    )
    while queues:
        for q in queues:
            ordered.append(q.pop(0))
        queues = [q for q in queues if q]
    for s in ordered:
        if resumed >= MAX_RESUME_PER_CYCLE:
            break
        if _process_session(state, s, now, skips):
            resumed += 1
    # skip 사유 집계 — 동일 사유 N개를 한 줄로 (resume_safety.log 스팸 방지)
    for reason, cnt in sorted(skips.items(), key=lambda kv: -kv[1]):
        _log(f"  skip {reason}" + (f" ×{cnt}" if cnt > 1 else ""))
    if resumed > 0:
        state["last_resume_at"] = now.isoformat()  # 10% cap: 재개 시각 기록 → 5h 쿨다운
    _save_state(state)
    return 0


def _acquire_lock():
    """단일 인스턴스 보장 — flock 비차단. 다른 인스턴스 실행 중이면 None 반환.

    launchd(매 5분) 사이클이 5분을 넘기면 인스턴스가 겹쳐 같은 세션을
    이중 fire → 토큰 드레인. flock 으로 한 번에 하나만 돌게 한다.
    (2026-06-08 fix)
    """
    fh = LOCK_FILE.open("w")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh  # 핸들 유지 → 프로세스 종료까지 락 보유
    except BlockingIOError:
        fh.close()
        return None


if __name__ == "__main__":
    _lock = _acquire_lock()
    if _lock is None:
        _log("⏭️ skip — 다른 안전망 인스턴스 실행 중 (flock)")
        sys.exit(0)
    try:
        sys.exit(main())
    except Exception as e:
        _log(f"💥 fatal — {type(e).__name__}: {e}")
        sys.exit(1)
