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
        tail = content[-1500:].lower()  # 마지막 화면만 (현재 상태)
        if not any(mk in tail for mk in LIMIT_MARKERS):
            continue
        # claude 류 화면인지 약하게 확인 (오탐 방지)
        if "resets" not in tail and "/upgrade" not in tail:
            continue
        # cooldown
        ls = last_sent.get(pane)
        if ls:
            try:
                if (now - datetime.fromisoformat(ls)).total_seconds() < COOLDOWN_SEC:
                    continue
            except Exception:
                pass
        reset = _parse_reset(tail, now)
        if reset is None:
            continue
        if now < reset:
            _log(f"  대기 {pane} — reset {reset.strftime('%H:%M')} 미도달")
            continue
        # 리셋 도달 → 계속 입력
        # ★ 사용자 수동 동작("stop 먼저 → 계속")을 그대로 재현 (06-20):
        #   1) Escape 로 멈춘 한도화면/생성중 상태 해제(stop) 2) 텍스트 입력 3) 별도 Enter.
        #   텍스트+Enter 를 한 번에 보내면 입력 버퍼 정착 전 제출돼 무시되던 문제 → 분리.
        _tmux("send-keys", "-t", pane, "Escape")
        time.sleep(0.5)
        _tmux("send-keys", "-t", pane, "Escape")  # 2회 — 메뉴/모달도 확실히 닫음
        time.sleep(0.5)
        _tmux("send-keys", "-t", pane, "계속 이어서 진행해줘. 확인 질문 없이 자율로.")
        time.sleep(0.4)
        _tmux("send-keys", "-t", pane, "Enter")
        last_sent[pane] = now.isoformat()
        fired += 1
        _log(f"  ▶️ send-keys 계속 — {pane} (reset {reset.strftime('%H:%M')} 도달)")
    if fired:
        _save_state(state)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        _log(f"💥 fatal — {type(e).__name__}: {e}")
        sys.exit(1)
