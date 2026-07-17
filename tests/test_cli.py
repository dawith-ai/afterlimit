"""CLI 헬퍼 테스트 — 잠금, 쿨다운 판정, run 사이클.

가장 위험한 부분은 잠금(스케줄러가 5분마다 겹쳐 호출)과 쿨다운(같은 세션 반복 재개)이다.
subprocess 는 config.dry_run 으로 차단해 claude 를 실제로 부르지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from afterlimit import cli
from afterlimit.config import Config
from afterlimit.limits import LimitInfo
from afterlimit.sessions import BlockedSession

SEOUL = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 7, 17, 20, 0, tzinfo=SEOUL)


def _session(sid="abc", reset_hour=19):
    reset = NOW.replace(hour=reset_hour, minute=0)
    return BlockedSession(
        session_id=sid,
        jsonl=None,  # 이 테스트에서는 쓰지 않음
        cwd="/work/repo",
        limit=LimitInfo("usage", reset),
        blocked_at=NOW - timedelta(hours=2),
        started_at=NOW - timedelta(hours=3),
    )


# ── 잠금 ────────────────────────────────────────────────────────────────

def test_잠금은_한_번만_잡힌다(tmp_path):
    cfg = Config(state_dir=tmp_path / "state")
    first = cli._acquire_lock(cfg)
    assert first is not None
    assert cli._acquire_lock(cfg) is None  # 두 번째는 실패
    first.unlink()
    assert cli._acquire_lock(cfg) is not None  # 풀린 뒤엔 다시 잡힘


def test_오래된_잠금은_자동_해제된다(tmp_path):
    import os

    cfg = Config(state_dir=tmp_path / "state")
    lock = cli._acquire_lock(cfg)
    assert lock is not None
    # 잠금을 아주 오래된 것으로 만든다 (timeout + 여유 이상)
    old = datetime.now().timestamp() - (cfg.invoke_timeout_sec + 120)
    os.utime(lock, (old, old))
    assert cli._acquire_lock(cfg) is not None  # stale → 재획득


# ── 쿨다운 판정 ─────────────────────────────────────────────────────────

def test_해제_전이면_대기(tmp_path):
    cfg = Config(state_dir=tmp_path / "state")
    s = _session(reset_hour=23)  # 아직 안 풀림 (지금 20시)
    assert cli._due(s, {}, cfg, NOW) is not None


def test_해제_후_기록_없으면_재개_가능(tmp_path):
    cfg = Config(state_dir=tmp_path / "state")
    s = _session(reset_hour=19)  # 풀림
    assert cli._due(s, {}, cfg, NOW) is None


def test_쿨다운_안에_재개했으면_건너뛴다(tmp_path):
    cfg = Config(state_dir=tmp_path / "state")
    s = _session(reset_hour=19)
    state = {"abc": {"resumed_at": (NOW - timedelta(hours=2)).isoformat()}}
    assert cli._due(s, state, cfg, NOW) is not None  # 5시간 쿨다운, 2시간 전 → 대기


def test_쿨다운_지났으면_재개_가능(tmp_path):
    cfg = Config(state_dir=tmp_path / "state")
    s = _session(reset_hour=19)
    state = {"abc": {"resumed_at": (NOW - timedelta(hours=6)).isoformat()}}
    assert cli._due(s, state, cfg, NOW) is None


def test_naive_타임스탬프_상태도_처리한다(tmp_path):
    """예전 상태 파일이 시간대 없는 값을 남겨도 TypeError 로 죽지 않는다."""
    cfg = Config(state_dir=tmp_path / "state")
    s = _session(reset_hour=19)
    state = {"abc": {"resumed_at": "2026-07-17T18:00:00"}}  # naive
    result = cli._due(s, state, cfg, NOW)  # 죽지 않으면 됨
    assert result is not None  # 2시간 전으로 해석 → 쿨다운


def test_손상된_타임스탬프는_쿨다운을_강제하지_않는다(tmp_path):
    cfg = Config(state_dir=tmp_path / "state")
    s = _session(reset_hour=19)
    state = {"abc": {"resumed_at": "쓰레기값"}}
    assert cli._due(s, state, cfg, NOW) is None


# ── 상태 저장 ───────────────────────────────────────────────────────────

def test_상태는_원자적으로_저장되고_다시_읽힌다(tmp_path):
    cfg = Config(state_dir=tmp_path / "state")
    cli._save_state(cfg, {"abc": {"resumed_at": NOW.isoformat()}})
    assert cli._load_state(cfg)["abc"]["resumed_at"] == NOW.isoformat()
    # 임시 파일이 남지 않는다
    assert not cfg.state_file.with_suffix(".tmp").exists()


def test_상태_파일이_없으면_빈_dict(tmp_path):
    cfg = Config(state_dir=tmp_path / "state")
    assert cli._load_state(cfg) == {}
