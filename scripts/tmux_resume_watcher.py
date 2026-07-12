#!/usr/bin/env python3
"""scripts/tmux_resume_watcher.py — tmux 안의 Claude Code 세션 자동 재개.

★ 사실: "Stop and wait for limit to reset"를 골라도 리셋 시 자동 재개 안 됨
   (Claude Code 미구현, GitHub #18980/#35744). 리셋 후 "continue"를 쳐야 이어짐.
★ 한도 UI 2종류 모두 처리:
   (A) 메뉴형   — "What do you want to do? / 1. Stop and wait / 2. Upgrade" → '1' 눌러 대기
   (B) 인라인형 — "You've hit your session limit · resets 1:20am" (프롬프트에 표시)
   두 경우 모두: 리셋 시각 도달 → "continue" 전송해 실제 재개.

== 모드 (~/.config/claude-terminal-auto/notify.json 의 "resume_mode") ==
- "token_only"(기본): 위 한도 재개만.  "keep_going": + 완료 후 유휴 세션 넛지.

== 설치 == launchd StartInterval 60. tmux 밖 세션엔 키 주입 불가.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.request
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
PROOF_FILE = Path("/tmp/openclaw_tmux_resume_PROOF.log")   # 한도→continue→재개 전 사이클 검증 기록
USAGE_CACHE = Path("/tmp/openclaw_usage_cache.json")       # API usage 캐시 (리셋시각·사용률)
USAGE_TTL = 120                                            # usage API 재조회 간격(초) — 효율
CONFIG_FILE = Path.home() / ".config" / "claude-terminal-auto" / "notify.json"
MENU_COOLDOWN = 120       # 같은 메뉴에 '1' 반복 방지
CONTINUE_COOLDOWN = 300   # 'continue' 재시도 간격
NUDGE_COOLDOWN = 900      # keep_going 넛지 간격
TAIL_CHARS = 700
BOTTOM_CHARS = 450        # 인라인 한도는 화면 하단만
SCAN_CHARS = 1400         # reset 시각은 넓게 검색

# (A) 인터랙티브 메뉴 시그니처 (셋 다)
MENU_SIGNS = ("stop and wait for", "limit to reset", "upgrade your plan")
# (B) 인라인 한도 메시지 시그니처 (하나라도, 화면 하단에)
INLINE_SIGNS = ("hit your session limit", "hit your usage limit", "session limit · resets",
                "5-hour limit reached", "weekly limit reached", "usage limit · resets")
GENERATING = "esc to interrupt"   # 작업중 신호
DIAG_SIGNS = ("what do you want to do", "limit to reset", "stop and wait", "hit your session limit",
              "usage limit", "5-hour limit", "weekly limit", "limit reached", "approaching", "resets ")
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


def _proof(msg: str) -> None:
    try:
        with PROOF_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now(KST).isoformat()}] ✅ 검증 {msg}\n")
    except Exception:
        pass


def _tmux(*args: str) -> str:
    try:
        return subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return ""


def _tmux_send(pane: str, text: str) -> None:
    _tmux("send-keys", "-t", pane, text)
    time.sleep(0.4)
    _tmux("send-keys", "-t", pane, "Enter")


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
    """리셋 시각 파싱. 시간만 있으면 now 에 '가장 가까운 발생'(어제/오늘/내일)을 고른다."""
    m = RESET_RE.search(text)
    if not m:
        return None
    hour = int(m.group(3))
    minute = int(m.group(4) or 0)
    if m.group(5).lower() == "pm" and hour != 12:
        hour += 12
    elif m.group(5).lower() == "am" and hour == 12:
        hour = 0
    if m.group(1) and m.group(2):   # "resets Jun 12 at 11pm" — 명시 날짜
        try:
            return datetime(now.year, _MONTH[m.group(1).lower()], int(m.group(2)), hour, minute, tzinfo=KST)
        except ValueError:
            return now + timedelta(hours=1)
    base = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return min((base - timedelta(days=1), base, base + timedelta(days=1)),
               key=lambda x: abs((x - now).total_seconds()))


def _oauth_token() -> str:
    """Claude OAuth 액세스 토큰 — cron 파일(resume-safety가 갱신) 우선, 없으면 키체인."""
    try:
        t = (Path.home() / ".claude" / "cron_oauth_token").read_text(encoding="utf-8").strip()
        if t.startswith("sk-ant-oat"):
            return t
    except Exception:
        pass
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                           capture_output=True, text=True, timeout=8)
        return json.loads(r.stdout.strip()).get("claudeAiOauth", {}).get("accessToken", "") or ""
    except Exception:
        return ""


def _api_usage() -> dict:
    """usage API에서 {reset: datetime|None, util: float|None} 반환. TTL 캐시(효율)."""
    try:
        c = json.loads(USAGE_CACHE.read_text(encoding="utf-8"))
        if time.time() - c.get("fetched", 0) < USAGE_TTL:
            rs = c.get("reset")
            return {"reset": datetime.fromisoformat(rs) if rs else None, "util": c.get("util")}
    except Exception:
        pass
    tok = _oauth_token()
    if not tok:
        return {"reset": None, "util": None}
    try:
        req = urllib.request.Request(
            "https://api.anthropic.com/api/oauth/usage",
            headers={"Authorization": f"Bearer {tok}", "anthropic-beta": "oauth-2025-04-20"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode())
        fh = data.get("five_hour") or {}
        rs = fh.get("resets_at")
        reset = datetime.fromisoformat(rs).astimezone(KST) if rs else None
        out = {"reset": reset, "util": fh.get("utilization")}
        try:
            USAGE_CACHE.write_text(json.dumps(
                {"fetched": time.time(), "reset": reset.isoformat() if reset else "", "util": out["util"]}))
        except Exception:
            pass
        return out
    except Exception:
        return {"reset": None, "util": None}


def main() -> int:
    if not _tmux("ls"):
        return 0
    now = datetime.now(KST)
    mode = _cfg("resume_mode", "token_only").lower()
    cont = _cfg("continue_prompt", "continue")
    state = _load_state()
    waits = state.setdefault("waits", {})
    idle = state.setdefault("idle", {})
    panes = _tmux("list-panes", "-a", "-F", "#{pane_id}").split()
    for pane in panes:
        content = _tmux("capture-pane", "-t", pane, "-p")
        if not content:
            continue
        low = content[-TAIL_CHARS:].lower()
        bottom = content[-BOTTOM_CHARS:].lower()
        scan = content[-SCAN_CHARS:].lower()
        if any(k in scan for k in DIAG_SIGNS):
            r = _parse_reset(scan, now)
            _diag(f"{pane} | menu={all(s in low for s in MENU_SIGNS)} | "
                  f"inline={any(s in bottom for s in INLINE_SIGNS)} | gen={GENERATING in low} | "
                  f"reset={r.strftime('%m-%d %H:%M') if r else '?'} | 화면:{content[-460:].strip()!r}")

        generating = GENERATING in low
        st = waits.get(pane)
        if generating:                       # 작업중 = 재개됨
            if st and st.get("last_try") and not st.get("proof_done"):
                # ★ 한도감지 → continue 전송 → 재개까지 전 사이클 검증 (제 말이 아닌 증거)
                _proof(f"{pane} | 한도감지 {(st.get('detected_at') or '?')[11:19]} "
                       f"→ continue 전송 {(st.get('last_try') or '?')[11:19]} "
                       f"→ 작업재개 확인 {now.strftime('%H:%M:%S')}")
                _messenger_notify(f"✅ [자동재개 검증완료] {pane} — 한도 걸림→continue 전송→작업 재개까지 "
                                  f"확인됨. 증거: /tmp/openclaw_tmux_resume_PROOF.log")
                st["proof_done"] = True
            if st:
                st["resumed"] = True
                waits[pane] = st
            continue

        menu_active = all(s in low for s in MENU_SIGNS)
        inline_active = any(s in bottom for s in INLINE_SIGNS)

        # ── 한도 감지 (메뉴 or 인라인) → 대기 등록 + 메뉴면 '1' ──
        if menu_active or inline_active:
            r = _api_usage()["reset"] or _parse_reset(scan, now)   # ★ API 정확 리셋시각 우선, 화면파싱 폴백
            r_iso = r.isoformat() if r else ""
            if not st or st.get("reset_at") != r_iso:      # 새 한도 창
                st = {"reset_at": r_iso, "detected_at": now.isoformat(),
                      "pressed1": None, "last_try": None, "resumed": False}
                waits[pane] = st
                _log(f"  ⏸ 한도 감지({'메뉴' if menu_active else '인라인'}) — {pane} · "
                     f"reset={r.strftime('%H:%M') if r else '?'}")
                _messenger_notify(f"⏸ Claude 한도 감지 — {pane}, 리셋"
                                  f"({r.strftime('%H:%M') if r else '?'}) 시 자동 continue 예정")
            if menu_active and not _cooled(st.get("pressed1"), MENU_COOLDOWN, now):
                _tmux_send(pane, "1")          # 메뉴 → 옵션1(대기)
                st["pressed1"] = now.isoformat()
                waits[pane] = st
                _log(f"  ▶️ 메뉴 '1'(대기) — {pane}")

        # ── 대기 상태 pane → reset 도달 시 'continue' (재개 전까지 재시도) ──
        if st and not st.get("resumed"):
            reset_at = st.get("reset_at")
            ready = True
            if reset_at:
                try:
                    ready = now >= datetime.fromisoformat(reset_at)
                except Exception:
                    ready = True
            if ready and not _cooled(st.get("last_try"), CONTINUE_COOLDOWN, now):
                _tmux_send(pane, cont)
                st["last_try"] = now.isoformat()
                waits[pane] = st
                _log(f"  ▶️ 리셋 도달 → '{cont}' 전송(재개 시도) — {pane}")
                _messenger_notify(f"⏯ Claude 리셋 도달 — {pane}에 '{cont}' 전송, 이전 작업 재개")
            continue

        # ── 재개 완료 + 한도문구 사라짐 → 상태 정리 ──
        if st and st.get("resumed") and not (menu_active or inline_active):
            waits.pop(pane, None)

        # ── keep_going: 완료 후 유휴 세션 넛지 (대기상태 아닐 때만) ──
        if mode == "keep_going" and not waits.get(pane):
            if _PROMPT_DRAFT.search(content[-400:]) or "bypass permissions" not in low:
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
