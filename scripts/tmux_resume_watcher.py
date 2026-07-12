#!/usr/bin/env python3
"""scripts/tmux_resume_watcher.py — tmux 안의 Claude Code 세션 자동 재개 (2단계).

★ 핵심 사실: Claude Code에서 한도 메뉴의 "Stop and wait for limit to reset"를
   선택해도 리셋 시 '자동으로 이어지지 않는다'(GitHub #18980/#35744, 미구현).
   리셋 후 사용자가 직접 "continue"를 입력해야 작업이 재개된다.
   → 이 watcher가 그 2단계를 대신 한다:

   [1단계] 한도 메뉴 감지 → "1"(Stop and wait) 눌러 '대기' 상태 진입 + reset 시각 파싱
   [2단계] reset 시각 도달 → "continue" 입력 → 실제로 이전 작업 재개

한도 메뉴:
    What do you want to do?
    ❯ 1. Stop and wait for limit to reset
      2. Upgrade your plan

== 모드 (설정: ~/.config/claude-terminal-auto/notify.json 의 "resume_mode") ==
- "token_only" (기본): 위 2단계(토큰 한도)만.
- "keep_going": 위에 더해, 완료 후 유휴 세션도 "계속 진행"으로 넛지(밤새 안 멈춤).

== 설치 == launchd StartInterval 60. tmux 밖 세션엔 키 주입 불가.
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
except Exception:
    def _messenger_notify(_m: str) -> list:
        return []

KST = timezone(timedelta(hours=9))
STATE_FILE = Path("/tmp/openclaw_tmux_resume_state.json")
LOG_FILE = Path("/tmp/openclaw_tmux_resume.log")
DIAG_FILE = Path("/tmp/openclaw_tmux_resume_DIAG.log")
CONFIG_FILE = Path.home() / ".config" / "claude-terminal-auto" / "notify.json"
MENU_COOLDOWN = 120       # 같은 메뉴에 '1' 반복 방지
CONTINUE_COOLDOWN = 300   # 'continue' 재전송 간격
FALLBACK_WAIT = 300       # reset 시각을 못 읽었을 때 대기 후 시도
NUDGE_COOLDOWN = 900      # keep_going 넛지 간격 (15분)
TAIL_CHARS = 700
SCAN_CHARS = 1400         # reset 시각은 넓게 검색

MENU_SIGNS = ("stop and wait for", "limit to reset", "upgrade your plan")
NOT_MENU = ("bypass permissions", "esc to interrupt")
DIAG_SIGNS = ("what do you want to do", "limit to reset", "stop and wait", "usage limit",
              "5-hour limit", "weekly limit", "limit reached", "approaching", "resets")
_PROMPT_DRAFT = re.compile(r"❯\s+\S")
RESET_RE = re.compile(
    r"resets?\s+(?:(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(\d{1,2})\s+at\s+)?"
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", re.IGNORECASE)
_MONTH = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


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


def _cfg(key: str, default: str) -> str:
    try:
        return str(json.loads(CONFIG_FILE.read_text(encoding="utf-8")).get(key, default)).strip() or default
    except Exception:
        return default


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


def _cooled(iso, secs, now) -> bool:
    if not iso:
        return False
    try:
        return (now - datetime.fromisoformat(iso)).total_seconds() < secs
    except Exception:
        return False


def _parse_reset(text: str, now: datetime):
    m = RESET_RE.search(text)
    if not m:
        return None
    hour = int(m.group(3))
    minute = int(m.group(4) or 0)
    if m.group(5).lower() == "pm" and hour != 12:
        hour += 12
    elif m.group(5).lower() == "am" and hour == 12:
        hour = 0
    if m.group(1) and m.group(2):  # "resets Jun 12 at 11pm"
        try:
            return datetime(now.year, _MONTH[m.group(1).lower()], int(m.group(2)), hour, minute, tzinfo=KST)
        except ValueError:
            return now + timedelta(hours=1)
    r = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if r < now - timedelta(hours=1):   # 자정 넘김 등: 한참 과거로 읽히면 내일로
        r += timedelta(days=1)
    return r


def _tmux_send(pane: str, text: str) -> None:
    _tmux("send-keys", "-t", pane, text)
    time.sleep(0.4)
    _tmux("send-keys", "-t", pane, "Enter")


def main() -> int:
    if not _tmux("ls"):
        return 0
    now = datetime.now(KST)
    mode = _cfg("resume_mode", "token_only").lower()
    cont = _cfg("continue_prompt", "continue")
    state = _load_state()
    waits = state.setdefault("waits", {})   # 토큰 한도 대기 상태
    idle = state.setdefault("idle", {})      # keep_going 유휴 추적
    panes = _tmux("list-panes", "-a", "-F", "#{pane_id}").split()
    for pane in panes:
        content = _tmux("capture-pane", "-t", pane, "-p")
        if not content:
            continue
        low = content[-TAIL_CHARS:].lower()
        scan = content[-SCAN_CHARS:].lower()
        if any(k in scan for k in DIAG_SIGNS):
            r = _parse_reset(scan, now)
            _diag(f"{pane} | menu={all(s in low for s in MENU_SIGNS)} | "
                  f"guard={[s for s in NOT_MENU if s in low] or '없음'} | "
                  f"reset={r.strftime('%m-%d %H:%M') if r else '?'} | 화면:{content[-500:].strip()!r}")

        st = waits.get(pane)
        # ── 1단계: 한도 메뉴 → '1'(Stop and wait) + reset 시각 확보 ──
        menu_active = all(s in low for s in MENU_SIGNS) and not any(s in low for s in NOT_MENU)
        if menu_active:
            if not (st and _cooled(st.get("menu_at"), MENU_COOLDOWN, now)):
                _tmux_send(pane, "1")
                r = _parse_reset(scan, now)
                waits[pane] = {"menu_at": now.isoformat(),
                               "reset_at": (r.isoformat() if r else ""),
                               "cont_at": None}
                _log(f"  ⏸ 1단계 한도메뉴 '1'(대기) — {pane} · reset={r.strftime('%H:%M') if r else '?'}")
                _messenger_notify(f"⏸ Claude 한도 감지 — {pane} 대기 진입, 리셋"
                                  f"({r.strftime('%H:%M') if r else '?'}) 시 자동 continue 예정")
            continue
        # ── 2단계: '대기' pane → reset 도달 시 'continue' 전송(실제 재개) ──
        if st:
            if "esc to interrupt" in low:      # 다시 생성중 = 재개됨 → 상태 클리어
                waits.pop(pane, None)
                continue
            reset_at = st.get("reset_at")
            if reset_at:
                try:
                    ready = now >= datetime.fromisoformat(reset_at)
                except Exception:
                    ready = True
            else:
                ready = not _cooled(st.get("menu_at"), FALLBACK_WAIT, now)
            if ready and not _cooled(st.get("cont_at"), CONTINUE_COOLDOWN, now):
                _tmux_send(pane, cont)
                st["cont_at"] = now.isoformat()
                waits[pane] = st
                _log(f"  ▶️ 2단계 리셋 도달 → '{cont}' 전송(작업 재개) — {pane}")
                _messenger_notify(f"⏯ Claude 리셋 도달 — {pane}에 '{cont}' 전송, 이전 작업 재개")
            continue
        # ── keep_going: 완료 후 유휴 세션 넛지 (선택 모드) ──
        if mode == "keep_going":
            if "esc to interrupt" in low or "bypass permissions" not in low:
                continue
            if _PROMPT_DRAFT.search(content[-400:]):   # 사용자 draft → 건드리지 않음
                idle[pane] = {"sig": content[-500:], "nudged": idle.get(pane, {}).get("nudged")}
                continue
            prev = idle.get(pane, {})
            sig = content[-500:]
            if prev.get("sig") == sig and not _cooled(prev.get("nudged"), NUDGE_COOLDOWN, now):
                _tmux_send(pane, "계속 진행해줘. 확인 질문 없이 자율로.")
                idle[pane] = {"sig": sig, "nudged": now.isoformat()}
                _log(f"  ▶️ keep_going 넛지 — {pane}")
                _messenger_notify(f"▶️ Claude 세션 자동 계속(keep_going) — {pane}")
            else:
                idle[pane] = {"sig": sig, "nudged": prev.get("nudged")}
    _save_state(state)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        _log(f"💥 fatal — {type(e).__name__}: {e}")
        sys.exit(1)
