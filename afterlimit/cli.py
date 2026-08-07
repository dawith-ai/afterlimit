"""afterlimit CLI — 스케줄러(launchd/systemd/cron)가 주기적으로 호출한다.

    afterlimit scan    무엇이 막혀 있고 언제 풀리는지 본다 (아무것도 실행하지 않음)
    afterlimit run     풀린 세션을 이어서 실행한다 (스케줄러가 호출하는 것)
    afterlimit config  현재 설정을 보여준다
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from afterlimit import __version__
from afterlimit.config import Config, config_path
from afterlimit.limits import local_tz
from afterlimit.notify import notify
from afterlimit.resume import resume
from afterlimit.sessions import BlockedSession, scan_blocked

__all__ = ["main"]


def _load_state(cfg: Config) -> dict:
    try:
        return json.loads(cfg.state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(cfg: Config, state: dict) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    tmp = cfg.state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(cfg.state_file)  # 원자적 교체 — 중간에 죽어도 파일이 깨지지 않는다


def _owner_alive(lock: Path) -> bool:
    """잠금을 만든 프로세스가 아직 살아 있나.

    확실히 죽었을 때만 False 를 준다. 읽을 수 없거나 남의 프로세스면 살아 있다고 본다 —
    PID 는 재사용되므로, 애매하면 잠금을 남기는 쪽이 안전하다(나이 제한이 결국 치운다).
    """
    try:
        pid = int(lock.read_text().strip())
    except (OSError, ValueError):
        return True
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)  # 신호 0 = 존재 확인만
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 살아 있는 남의 프로세스
    return True


def _acquire_lock(cfg: Config) -> Path | None:
    """단일 인스턴스 보장. 스케줄러가 겹쳐 실행해도 두 번 재개하지 않는다."""
    cfg.lock_dir.mkdir(parents=True, exist_ok=True)
    lock = cfg.lock_dir / "run.lock"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(lock, flags, 0o600)
    except FileExistsError:
        try:
            age = datetime.now().timestamp() - lock.stat().st_mtime
        except OSError:
            return None
        # 주인이 죽었으면 나이와 상관없이 치운다.
        # 왜: 실행 도중 죽으면(스케줄러 재적재·재부팅·강제종료) 잠금이 남아 16분간
        # 무인 재개가 통째로 멎는다. 2026-08-07 잡을 껐다 켜자 실제로 그렇게 됐다.
        if _owner_alive(lock) and age < cfg.invoke_timeout_sec + 60:
            return None  # 아직 돌고 있다
        lock.unlink(missing_ok=True)  # 죽은 채 남은 잠금 — 치운다
        try:
            fd = os.open(lock, flags, 0o600)
        except FileExistsError:
            return None  # 다른 인스턴스가 방금 선점함
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return lock


#: 재개했는데 한도가 그대로일 때 다음 시도까지 기다리는 시간. 실패가 쌓일수록 배로 늘린다.
#:
#: 왜 필요한가: 실패를 기록하지 않던 시절, 한도가 안 풀린 세션을 매 사이클(30분) 무한히
#: 두드렸다. 하룻밤에 재개 139건 중 137건이 그대로 튕겼다(실제로 이어간 건 2건).
#: 재개가 안 되는 세션일수록 더 자주 시도하는 구조였다.
_BACKOFF = [timedelta(hours=h) for h in (1, 2, 4, 8, 16)]
_BACKOFF_MAX = timedelta(hours=24)


def backoff_for(fails: int) -> timedelta:
    """실패 `fails` 회 뒤 기다릴 시간. 1·2·4·8·16시간으로 늘고 24시간에서 멈춘다."""
    if fails < 1:
        return timedelta(0)
    return _BACKOFF[fails - 1] if fails <= len(_BACKOFF) else _BACKOFF_MAX


def _at(raw: object, now: datetime) -> datetime | None:
    """상태 파일의 시각 문자열을 읽는다. 손상됐으면 None — 쿨다운을 강제하지 않는다."""
    if not isinstance(raw, str):
        return None
    try:
        t = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    return t.replace(tzinfo=now.tzinfo) if t.tzinfo is None else t  # 예전 naive 기록 보정


#: 세션 id 가 아닌 전역 상태 자리. UUID 와 겹치지 않는다.
_GLOBAL = "_global"


def _spend_wait(state: dict, now: datetime) -> str | None:
    """계정 지출 한도로 막혀 있는 동안의 전역 대기.

    지출 한도는 **계정 전체** 조건이다. 한 세션이 튕겼으면 나머지도 전부 튕긴다.
    세션마다 따로 배우게 두면 76개가 각자 부딪혀 하룻밤에 300번을 헛되이 태운다
    (2026-08-08 실측: 최근 100회 시도 중 성공 1건). 한 번 튕기면 전부 같이 쉰다.
    간격은 세션별 백오프와 같다 — 1·2·4·8·16시간, 최대 24시간.
    """
    e = state.get(_GLOBAL, {})
    at = _at(e.get("spend_failed_at"), now)
    if not at:
        return None
    fails = int(e.get("spend_fails", 1) or 1)
    left = backoff_for(fails) - (now - at)
    if left > timedelta(0):
        return (f"계정 지출 한도 {fails}회 확인됨 · "
                f"{left.total_seconds() / 3600:.1f}시간 뒤 다시 확인 (/usage-credits)")
    return None


def _due(session: BlockedSession, state: dict, cfg: Config, now: datetime) -> str | None:
    """재개하면 안 되는 이유. None 이면 해도 된다."""
    if not session.limit.is_over(now):
        when = session.limit.reset_at
        if when:
            return f"아직 안 풀림 (해제 {when:%H:%M})"
        # 지출 한도엔 해제 시각이 없다. 시각으로 판단할 수 없으니 아래 **백오프**가 속도를 정한다.
        #
        # ⚠️ 자동 재개에서 통째로 빼면 안 된다(2026-08-07 실수). 한 번 그렇게 했더니 막힌
        #    세션이 전부 지출 한도라 재개가 0건이 됐다 — 헛발질은 막았지만 작업 이어가기도 죽었다.
        #    지출 한도는 간헐적으로 열린다(그날 밤 282초·225초짜리 실제 작업이 실제로 이어졌다).
        #    두드려봐야 열린 걸 안다. 다만 열릴 때까지 매 사이클 두드리면 안 되므로,
        #    실패 1회당 1·2·4·8·16시간, 최대 24시간으로 간격을 벌린다.
        if session.limit.kind != "spend":
            return "해제 시각 불명"
        if reason := _spend_wait(state, now):
            return reason

    entry = state.get(session.session_id, {})

    if prev := _at(entry.get("resumed_at"), now):
        gap = now - prev
        if gap < timedelta(hours=cfg.resume_cooldown_hours):
            return f"최근에 재개함 ({gap.total_seconds() / 3600:.1f}시간 전)"

    # 재개는 했는데 한도가 그대로였던 경우. 재우지 않으면 같은 세션을 매 사이클 두드린다.
    if failed := _at(entry.get("failed_at"), now):
        fails = int(entry.get("fails", 1) or 1)
        left = backoff_for(fails) - (now - failed)
        if left > timedelta(0):
            return f"한도 그대로였음 {fails}회 · {left.total_seconds() / 3600:.1f}시간 더 기다림"
    return None


def _fmt(session: BlockedSession) -> str:
    reset = session.limit.reset_at
    when = f"{reset:%m-%d %H:%M}" if reset else "불명"
    return f"{session.project}/{session.session_id[:8]}  {session.limit.kind:<11} 해제 {when}"


def cmd_scan(cfg: Config) -> int:
    now = datetime.now(local_tz())
    blocked = scan_blocked(cfg, now)
    if not blocked:
        print("한도로 멈춘 세션이 없습니다.")
        return 0

    state = _load_state(cfg)
    print(f"멈춘 세션 {len(blocked)}개 (현재 {now:%m-%d %H:%M %Z})\n")
    for s in blocked:
        reason = _due(s, state, cfg, now)
        mark = "대기" if reason else "재개 가능"
        print(f"  [{mark}] {_fmt(s)}")
        if reason:
            print(f"           └ {reason}")
    return 0


def cmd_run(cfg: Config) -> int:
    lock = _acquire_lock(cfg)
    if lock is None:
        print("이미 실행 중입니다.", file=sys.stderr)
        return 0

    try:
        now = datetime.now(local_tz())
        state = _load_state(cfg)
        resumed = 0

        for session in scan_blocked(cfg, now):
            if resumed >= cfg.max_resume_per_cycle:
                break
            if reason := _due(session, state, cfg, now):
                print(f"건너뜀 {_fmt(session)} — {reason}")
                continue

            print(f"재개 {_fmt(session)}")
            result = resume(session, cfg)
            resumed += 1

            entry = state.setdefault(session.session_id, {})
            entry["project"] = session.project

            if result.hit_limit_again:
                # 실제로 이어간 게 아니니 성공 쿨다운은 안 준다. 대신 실패를 세어 재운다.
                # 이걸 기록하지 않으면 다음 사이클에 "재개한 적 없음"으로 보여 또 두드린다.
                entry["fails"] = int(entry.get("fails", 0) or 0) + 1
                entry["failed_at"] = now.isoformat()
                if session.limit.kind == "spend":
                    # 계정 전체 조건이므로 전역에도 적어 나머지 세션까지 같이 재운다
                    g = state.setdefault(_GLOBAL, {})
                    g["spend_fails"] = int(g.get("spend_fails", 0) or 0) + 1
                    g["spend_failed_at"] = now.isoformat()
                _save_state(cfg, state)
                wait = backoff_for(entry["fails"]).total_seconds() / 3600
                print(f"  └ 아직 한도가 풀리지 않았습니다({entry['fails']}회). "
                      f"{wait:.0f}시간 뒤에 다시 봅니다.")
                continue

            entry["resumed_at"] = now.isoformat()
            entry.pop("fails", None)  # 이어갔으면 실패 기록을 지운다
            entry.pop("failed_at", None)
            state.pop(_GLOBAL, None)  # 한도가 풀렸다는 증거 — 전역 대기도 푼다
            _save_state(cfg, state)

            how = "새로 시작" if result.fallback else "이어감"
            status = "완료" if result.ok else f"실패: {result.error.strip()[:120]}"
            print(f"  └ {how} · {result.elapsed_sec:.0f}초 · {status}")
            notify(cfg, f"[afterlimit] {session.project} {how} — {status}")

        if resumed == 0:
            print("재개할 세션이 없습니다.")
        return 0
    finally:
        lock.unlink(missing_ok=True)


def cmd_config(cfg: Config) -> int:
    print(f"설정 파일: {config_path()}{'' if config_path().exists() else '  (없음 — 기본값 사용)'}")
    print(f"세션 기록: {cfg.projects_dir}")
    print(f"상태 저장: {cfg.state_dir}")
    print(f"claude 실행 파일: {cfg.claude_bin}")
    print(f"시간대: {datetime.now(local_tz()):%Z (%z)}")
    print(f"알림 웹훅: {'설정됨' if cfg.webhook_url else '없음'}")
    print(f"사이클당 최대 재개: {cfg.max_resume_per_cycle}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="afterlimit",
        description="AI 코딩 에이전트가 사용량 한도로 멈춰도, 풀리면 스스로 이어가게 합니다.",
    )
    parser.add_argument("--config", type=Path, help="설정 파일 경로")
    parser.add_argument("--dry-run", action="store_true", help="실제로 재개하지 않고 계획만 출력")
    parser.add_argument("--version", action="version", version=f"afterlimit {__version__}")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("scan", help="막힌 세션과 해제 시각 보기")
    sub.add_parser("run", help="풀린 세션 이어서 실행 (스케줄러가 호출)")
    sub.add_parser("config", help="현재 설정 보기")

    args = parser.parse_args(argv)
    try:
        cfg = Config.load(args.config)
    except ValueError as e:
        print(f"설정 오류: {e}", file=sys.stderr)
        return 2
    from dataclasses import replace

    if args.dry_run:
        cfg = replace(cfg, dry_run=True)

    return {"scan": cmd_scan, "run": cmd_run, "config": cmd_config}.get(
        args.cmd or "scan", cmd_scan
    )(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
