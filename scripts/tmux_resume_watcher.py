#!/usr/bin/env python3
"""scripts/tmux_resume_watcher.py — tmux 안의 Claude Code 세션 자동 재개.

목적: 사용량 한도에 걸리면 Claude Code가 이런 결정 메뉴를 띄운다:

    What do you want to do?
    ❯ 1. Stop and wait for limit to reset
      2. Upgrade your plan
    Enter to confirm · Esc to cancel

사용자가 자리에 없어도 옵션 1("Stop and wait for limit to reset")을 자동 확정해서,
한도 리셋 시각에 작업이 그대로 이어지게 한다. (사용자 수동동작 '1 → Enter' 재현)

== 동작 ==
1. tmux 모든 pane 을 capture-pane 로 스캔
2. 위 결정 메뉴가 '현재 활성'으로 하단에 떠 있으면 → "1" 선택 후 Enter
3. pane별 cooldown 5분 (같은 메뉴 중복 입력 방지)
4. notify.py 설정돼 있으면 메신저 알림

== 오탐 차단 (중요) ==
- 메뉴 세 문구(stop and wait for / limit to reset / upgrade your plan) 가 모두 있어야 함
- 일반 입력바("bypass permissions") 나 생성중("esc to interrupt") 이 보이면 skip
  → 대화에 메뉴 문구가 '인용'만 된 경우(활성 메뉴 아님)를 걸러낸다.

== 설치 ==
launchd StartInterval 60. tmux 밖 세션엔 키 주입 불가(macOS TIOCSTI 차단).
"""
from __future__ import annotations

import json
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
COOLDOWN_SEC = 300   # pane별 — 같은 메뉴 반복 입력 방지
TAIL_CHARS = 700     # 현재 화면 하단만 검사

# 한도 결정 메뉴 시그니처 (셋 다 있어야 = 그 메뉴)
MENU_SIGNS = ("stop and wait for", "limit to reset", "upgrade your plan")
# 이게 하단에 있으면 '활성 메뉴' 가 아님 → skip (일반 입력바 / 생성중)
NOT_MENU = ("bypass permissions", "esc to interrupt")


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


def main() -> int:
    if not _tmux("ls"):
        return 0
    now = datetime.now(KST)
    state = _load_state()
    last_sent = state.setdefault("last_sent", {})
    panes = _tmux("list-panes", "-a", "-F", "#{pane_id}").split()
    fired = 0
    for pane in panes:
        content = _tmux("capture-pane", "-t", pane, "-p")
        if not content:
            continue
        low = content[-TAIL_CHARS:].lower()
        # 한도 결정 메뉴가 '활성'으로 떠 있나 (세 문구 다 + 일반바/생성중 아님)
        if not all(s in low for s in MENU_SIGNS):
            continue
        if any(s in low for s in NOT_MENU):
            continue
        # pane별 cooldown (같은 메뉴 중복 입력 방지)
        st = last_sent.get(pane)
        if isinstance(st, dict):
            try:
                if (now - datetime.fromisoformat(st.get("at", ""))).total_seconds() < COOLDOWN_SEC:
                    continue
            except Exception:
                pass
        # ★ 옵션1 "Stop and wait for limit to reset" 선택 + 확정 (사용자 수동 '1 → Enter' 재현)
        #   → Claude 가 한도 리셋 시각에 작업을 자동으로 이어감. (Esc 금지: 메뉴 취소됨)
        _tmux("send-keys", "-t", pane, "1")
        time.sleep(0.4)
        _tmux("send-keys", "-t", pane, "Enter")
        last_sent[pane] = {"at": now.isoformat()}
        fired += 1
        _log(f"  ▶️ 한도메뉴 옵션1 확정(대기 → 리셋 시 자동재개) — {pane}")
        ch = _messenger_notify(f"⏯ Claude 한도 메뉴 자동확정 — {pane} (Stop & wait → 리셋 시 자동 이어감)")
        if ch:
            _log(f"     📨 알림 전송: {', '.join(ch)}")
    if fired:
        _save_state(state)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        _log(f"💥 fatal — {type(e).__name__}: {e}")
        sys.exit(1)
