#!/usr/bin/env python3
"""scripts/tmux_resume_watcher.py — tmux 안의 Claude Code 세션 자동 재개.

한도에 걸리면 뜨는 결정 메뉴에서 옵션1을 자동 선택해, 리셋 시각에 작업이 이어지게 한다:

    What do you want to do?
    ❯ 1. Stop and wait for limit to reset
      2. Upgrade your plan

== 모드 (설정: ~/.config/claude-terminal-auto/notify.json 의 "resume_mode") ==
- "token_only" (기본·효율): 토큰 한도 메뉴만 자동 처리. 작업 끝나면 멈춤(토큰 아낌).
- "keep_going" (자율): 위에 더해, 완료 후 '유휴'인 세션도 '계속 진행'으로 넛지(밤새 안 멈춤).
    안전장치 — 생성중/입력창에 사용자 draft 있으면 건드리지 않음, pane당 15분 쿨다운.

== 설치 ==
launchd StartInterval 60. tmux 밖 세션엔 키 주입 불가(macOS TIOCSTI 차단).
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
DIAG_FILE = Path("/tmp/openclaw_tmux_resume_DIAG.log")
CONFIG_FILE = Path.home() / ".config" / "claude-terminal-auto" / "notify.json"
COOLDOWN_SEC = 300         # 메뉴 반복 입력 방지
NUDGE_COOLDOWN_SEC = 900   # keep_going 넛지 반복 방지 (15분)
TAIL_CHARS = 700

# 토큰 한도 결정 메뉴 시그니처 (셋 다 있어야)
MENU_SIGNS = ("stop and wait for", "limit to reset", "upgrade your plan")
# 이게 하단에 있으면 '활성 메뉴'가 아님 (일반 입력바 / 생성중)
NOT_MENU = ("bypass permissions", "esc to interrupt")
# 진단용 — 한도 '비슷한' 신호가 있으면 원문을 남긴다
DIAG_SIGNS = ("what do you want to do", "limit to reset", "stop and wait",
              "usage limit", "5-hour limit", "weekly limit", "approaching", "/upgrade")
# 입력창에 사용자 draft가 있는지 (❯ 뒤에 글자) → keep_going 이 건드리지 않도록
_PROMPT_DRAFT = re.compile(r"❯\s+\S")


def _log(msg: str) -> None:
    line = f"[{datetime.now(KST).isoformat()}] {msg}\n"
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    print(line.strip())


def _diag(msg: str) -> None:
    try:
        with DIAG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now(KST).isoformat()}] {msg}\n")
    except Exception:
        pass


def _tmux(*args: str) -> str:
    try:
        return subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return ""


def _config_mode() -> str:
    """resume_mode 설정 읽기. token_only(기본) | keep_going."""
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        m = str(cfg.get("resume_mode", "token_only")).strip().lower()
        return m if m in ("token_only", "keep_going") else "token_only"
    except Exception:
        return "token_only"


def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _cooled(iso: str | None, secs: int, now: datetime) -> bool:
    """iso 시각 이후 secs 초가 아직 안 지났으면 True(쿨다운 중)."""
    if not iso:
        return False
    try:
        return (now - datetime.fromisoformat(iso)).total_seconds() < secs
    except Exception:
        return False


def _handle_menu(pane: str, low: str, now: datetime, menu_state: dict) -> bool:
    """토큰 한도 메뉴면 옵션1(1+Enter) 확정. 처리 대상이면 True(다른 처리 skip)."""
    if not all(s in low for s in MENU_SIGNS):
        return False
    if any(s in low for s in NOT_MENU):
        return False
    if _cooled(menu_state.get(pane), COOLDOWN_SEC, now):
        return True  # 방금 눌렀음 → 재입력 방지, 다른 처리도 skip
    _tmux("send-keys", "-t", pane, "1")
    time.sleep(0.4)
    _tmux("send-keys", "-t", pane, "Enter")
    menu_state[pane] = now.isoformat()
    _log(f"  ▶️ 한도메뉴 옵션1 확정(대기 → 리셋 시 자동재개) — {pane}")
    _messenger_notify(f"⏯ Claude 한도 메뉴 자동확정 — {pane} (Stop & wait → 리셋 시 자동 이어감)")
    return True


def _handle_idle_nudge(pane: str, content: str, low: str, now: datetime, idle_state: dict) -> None:
    """keep_going: 완료 후 '유휴'인 세션을 '계속 진행'으로 넛지 (안전장치 포함)."""
    if "esc to interrupt" in low:          # 생성중 → 유휴 아님
        return
    if "bypass permissions" not in low:    # 일반 유휴 프롬프트가 아님
        return
    if _PROMPT_DRAFT.search(content[-400:]):  # 사용자 draft 있음 → 건드리지 않음
        idle_state[pane] = {"sig": content[-500:], "nudged": idle_state.get(pane, {}).get("nudged")}
        return
    prev = idle_state.get(pane, {})
    sig = content[-500:]
    stable = prev.get("sig") == sig        # 직전 사이클과 동일 = 진짜 유휴(렌더 중 아님)
    if stable and not _cooled(prev.get("nudged"), NUDGE_COOLDOWN_SEC, now):
        _tmux("send-keys", "-t", pane, "계속 진행해줘. 확인 질문 없이 자율로.")
        time.sleep(0.4)
        _tmux("send-keys", "-t", pane, "Enter")
        idle_state[pane] = {"sig": sig, "nudged": now.isoformat()}
        _log(f"  ▶️ keep_going 넛지(계속 진행) — {pane}")
        _messenger_notify(f"▶️ Claude 세션 자동 계속(keep_going) — {pane}")
    else:
        idle_state[pane] = {"sig": sig, "nudged": prev.get("nudged")}


def main() -> int:
    if not _tmux("ls"):
        return 0
    now = datetime.now(KST)
    mode = _config_mode()
    state = _load_state()
    menu_state = state.setdefault("menu", {})
    idle_state = state.setdefault("idle", {})
    panes = _tmux("list-panes", "-a", "-F", "#{pane_id}").split()
    for pane in panes:
        content = _tmux("capture-pane", "-t", pane, "-p")
        if not content:
            continue
        low = content[-TAIL_CHARS:].lower()
        # 진단: 한도 '비슷한' 신호가 있으면 발사 여부와 무관하게 원문 기록
        if any(k in low for k in DIAG_SIGNS):
            has3 = all(s in low for s in MENU_SIGNS)
            guard = [s for s in NOT_MENU if s in low]
            _diag(f"{pane} | 메뉴3문구={has3} | 가드차단={guard or '없음'} | "
                  f"발사={'예' if (has3 and not guard) else '아니오'} | 화면: {content[-450:].strip()!r}")
        # 1) 토큰 한도 메뉴 (모든 모드 공통)
        if _handle_menu(pane, low, now, menu_state):
            continue
        # 2) keep_going 모드에서만: 완료 후 유휴 세션 넛지
        if mode == "keep_going":
            _handle_idle_nudge(pane, content, low, now, idle_state)
    _save_state(state)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        _log(f"💥 fatal — {type(e).__name__}: {e}")
        sys.exit(1)
