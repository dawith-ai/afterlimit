#!/usr/bin/env python3
"""scripts/tmux_resume_watcher.py — tmux 안의 claude 세션 자동 재개.

목적: 사용자가 보는 대화형 claude 세션이 한도("session limit · resets HH:MMam")에
걸려 멈추면, 리셋 시각 도달 시 자동으로 "계속"을 입력해 화면째로 이어가게 한다.
(외부 안전망 resume_blocked_sessions.py 는 백그라운드 새 세션으로 일을 잇지만,
 보이는 터미널은 안 풀린다 — 이 watcher 가 그 빈틈을 메운다.)

== 동작 ==
1. tmux 모든 pane 내용(capture-pane) 스캔
2. 마지막 화면에 한도 메시지 + reset 시각 있으면 추출
3. 현재 ≥ reset → `tmux send-keys -t <pane> "계속" Enter` 1회
4. pane 별 cooldown 10분 (중복 입력 방지)

== 설치 ==
launchd com.openclaw.tmux-resume (StartInterval 60). tmux 밖 세션엔 못 함(macOS TIOCSTI 차단).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from notify import notify as _messenger_notify
except Exception:  # notify.py 없거나 오류 → 알림만 조용히 생략
    def _messenger_notify(_m: str) -> list:
        return []

KST = timezone(timedelta(hours=9))
STATE_FILE = Path("/tmp/openclaw_tmux_resume_state.json")
LOG_FILE = Path("/tmp/openclaw_tmux_resume.log")
COOLDOWN_SEC = 600  # pane 별 10분 — 같은 멈춤에 반복 입력 방지
RESET_RE = re.compile(
    r"resets?\s+(?:(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(\d{1,2})\s+at\s+)?"
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)",
    re.IGNORECASE,
)
_MONTH = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
          "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
# 한도 메시지 시그니처 (claude CLI 가 출력하는 막힘 문구)
LIMIT_MARKERS = ("hit your session limit", "session limit", "usage limit",
                 "5-hour limit", "weekly limit")
# 활성 작업중 시그니처 — 이게 보이면 '멈춘' 게 아니라 '돌고 있는' 세션 → 발사 금지 (오탐 차단)
ACTIVE_MARKERS = ("esc to interrupt", "esc to cancel", "tokens)", "cooked for",
                  "cascading", "deliberating", "gesticulating", "churned", "thinking")
TAIL_CHARS = 600   # 현재 화면 하단만 검사 (넓으면 스크롤백에 남은 옛 한도문구에 오탐)
MAX_ATTEMPTS = 2   # 같은 reset 창 최대 시도 — 넘으면 무한루프 대신 '수동필요' 알림
RETRY_SEC = 120    # 같은 창 재시도 최소 간격(초)


def _log(msg: str) -> None:
    line = f"[{datetime.now(KST).isoformat()}] {msg}\n"
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    print(line.strip())


def _tmux(*args: str) -> str:
    try:
        return subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return ""


def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"last_sent": {}}


def _save_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _parse_reset(text: str, now: datetime) -> datetime | None:
    """한도 메시지에서 reset 시각 추출. 없으면 None."""
    m = RESET_RE.search(text)
    if not m:
        return None
    hour = int(m.group(3))
    minute = int(m.group(4) or 0)
    ampm = m.group(5).lower()
    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    if m.group(1) and m.group(2):  # 주간 형식 "resets Jun 12 at 11pm"
        try:
            reset = datetime(now.year, _MONTH[m.group(1).lower()], int(m.group(2)),
                             hour, minute, tzinfo=KST)
        except ValueError:
            return now + timedelta(hours=1)
        if reset < now - timedelta(days=180):
            reset = reset.replace(year=now.year + 1)
        return reset
    # 시간만 — 오늘 그 시각, 이미 지났으면 24h 내로 간주(보통 곧 도달)
    reset = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if reset > now + timedelta(hours=2):
        # reset 이 한참 미래면 어제 시각이 풀린 것 → 이미 도달로 처리
        reset -= timedelta(days=1)
    return reset


def main() -> int:
    # tmux 떠 있나
    if not _tmux("ls"):
        return 0
    now = datetime.now(KST)
    state = _load_state()
    last_sent = state.setdefault("last_sent", {})
    # 모든 pane id 나열
    panes = _tmux("list-panes", "-a", "-F", "#{pane_id}").split()
    fired = 0
    for pane in panes:
        content = _tmux("capture-pane", "-t", pane, "-p")
        if not content:
            continue
        low = content[-TAIL_CHARS:].lower()  # 현재 화면 하단만 (스크롤백 잔재 오탐 방지)
        if not any(mk in low for mk in LIMIT_MARKERS):
            continue
        if "resets" not in low and "/upgrade" not in low:
            continue
        # ★ 활성 작업중이면 skip — 한도로 '멈춘' 게 아니라 '돌고 있는' 세션 (오탐 핵심 차단)
        if any(w in low for w in ACTIVE_MARKERS):
            continue
        reset = _parse_reset(low, now)
        if reset is None:
            continue
        if now < reset:
            _log(f"  대기 {pane} — reset {reset.strftime('%H:%M')} 미도달")
            continue
        # ★ 무한루프 차단: 같은 reset 창엔 최대 MAX_ATTEMPTS 회만 시도 (구버전 str state 호환)
        rk = reset.isoformat()
        st = last_sent.get(pane)
        if not isinstance(st, dict):
            st = {}
        if st.get("reset") == rk:
            if st.get("attempts", 0) >= MAX_ATTEMPTS:
                continue  # 이미 최대 시도·'수동필요' 알림 완료 → 조용히 skip
            try:
                if (now - datetime.fromisoformat(st["at"])).total_seconds() < RETRY_SEC:
                    continue
            except Exception:
                pass
            attempts = st.get("attempts", 0)
        else:
            attempts = 0
        # 리셋 도달 → 계속 입력 (Escape 2회로 멈춤 해제 → 텍스트 → 별도 Enter)
        _tmux("send-keys", "-t", pane, "Escape")
        time.sleep(0.5)
        _tmux("send-keys", "-t", pane, "Escape")
        time.sleep(0.5)
        _tmux("send-keys", "-t", pane, "계속 이어서 진행해줘. 확인 질문 없이 자율로.")
        time.sleep(0.4)
        _tmux("send-keys", "-t", pane, "Enter")
        attempts += 1
        last_sent[pane] = {"reset": rk, "attempts": attempts, "at": now.isoformat()}
        fired += 1
        _log(f"  ▶️ send-keys 계속 — {pane} (reset {reset.strftime('%H:%M')} 도달, 시도 {attempts}/{MAX_ATTEMPTS})")
        if attempts >= MAX_ATTEMPTS:
            _log(f"  ⚠️ {pane} — {MAX_ATTEMPTS}회 시도해도 안 풀림. 수동 확인 필요")
            _messenger_notify(
                f"⚠️ Claude 터미널 {pane} 자동재개 {MAX_ATTEMPTS}회 실패 — 수동 확인 필요 (reset {reset.strftime('%H:%M')})"
            )
        else:
            _messenger_notify(
                f"⏯ Claude 터미널 자동 재개 시도 — {pane} (reset {reset.strftime('%H:%M')})"
            )
    if fired:
        _save_state(state)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        _log(f"💥 fatal — {type(e).__name__}: {e}")
        sys.exit(1)
