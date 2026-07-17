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


def _acquire_lock(cfg: Config) -> Path | None:
    """단일 인스턴스 보장. 스케줄러가 겹쳐 실행해도 두 번 재개하지 않는다."""
    cfg.lock_dir.mkdir(parents=True, exist_ok=True)
    lock = cfg.lock_dir / "run.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            age = datetime.now().timestamp() - lock.stat().st_mtime
        except OSError:
            return None
        if age < cfg.invoke_timeout_sec + 60:
            return None  # 아직 돌고 있다
        lock.unlink(missing_ok=True)  # 죽은 채 남은 잠금 — 치운다
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return None
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return lock


def _due(session: BlockedSession, state: dict, cfg: Config, now: datetime) -> str | None:
    """재개하면 안 되는 이유. None 이면 해도 된다."""
    if not session.limit.is_over(now):
        when = session.limit.reset_at
        return f"아직 안 풀림 (해제 {when:%H:%M})" if when else "해제 시각 불명"
    last = state.get(session.session_id, {}).get("resumed_at")
    if last:
        try:
            gap = now - datetime.fromisoformat(last)
        except ValueError:
            return None
        if gap < timedelta(hours=cfg.resume_cooldown_hours):
            return f"최근에 재개함 ({gap.total_seconds() / 3600:.1f}시간 전)"
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

            if result.hit_limit_again:
                print("  └ 아직 한도가 풀리지 않았습니다. 다음 사이클에 다시 봅니다.")
                continue  # 쿨다운을 기록하지 않는다 — 실제로 이어간 게 아니다

            state.setdefault(session.session_id, {})["resumed_at"] = now.isoformat()
            state[session.session_id]["project"] = session.project
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
    if args.dry_run:
        from dataclasses import replace

        cfg = replace(cfg, dry_run=True)

    return {"scan": cmd_scan, "run": cmd_run, "config": cmd_config}.get(
        args.cmd or "scan", cmd_scan
    )(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
