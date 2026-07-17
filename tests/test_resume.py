"""재개 실행 테스트 — subprocess 는 목으로 막고, 폴백·한도재발·타임아웃을 검증한다."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from afterlimit.config import Config
from afterlimit.limits import LimitInfo
from afterlimit.resume import ResumeResult, resume
from afterlimit.sessions import BlockedSession

SEOUL = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 7, 17, 20, 0, tzinfo=SEOUL)


def _session():
    return BlockedSession(
        session_id="abc",
        jsonl=None,
        cwd="/work/repo",
        limit=LimitInfo("usage", NOW.replace(hour=19)),
        blocked_at=NOW - timedelta(hours=1),
        started_at=NOW - timedelta(hours=2),
        last_user="테스트 고쳐줘",
        last_assistant="고치는 중",
    )


def test_dry_run은_아무것도_실행하지_않는다():
    cfg = Config(dry_run=True)
    with patch("afterlimit.resume._run") as run:
        r = resume(_session(), cfg)
        run.assert_not_called()
    assert r.ok and not r.fallback


def test_resume_성공():
    cfg = Config()
    with patch("afterlimit.resume._run", return_value=(0, "완료했습니다", "", 3.0)) as run:
        r = resume(_session(), cfg)
    assert r.ok and not r.fallback
    # --resume 경로를 썼는지
    assert "--resume" in run.call_args[0][0]


def test_구조적_실패시_fresh_폴백():
    cfg = Config()
    # 1차 --resume 는 rc!=0 + 빈 출력 (세션 못 찾음), 2차 fresh 는 성공
    outcomes = [(1, "", "no session", 1.0), (0, "새로 했습니다", "", 2.0)]
    with patch("afterlimit.resume._run", side_effect=outcomes) as run:
        r = resume(_session(), cfg)
    assert r.ok and r.fallback
    assert run.call_count == 2
    # 폴백에는 이전 맥락이 프롬프트에 실린다
    assert "테스트 고쳐줘" in run.call_args[0][0][2]


def test_또_한도에_걸리면_폴백하지_않는다():
    """한도 재발은 구조적 실패가 아니다 — 새로 시작하면 안 되고 그대로 반환한다."""
    cfg = Config()
    with patch("afterlimit.resume._run", return_value=(1, "usage limit reached", "", 1.0)) as run:
        r = resume(_session(), cfg)
    assert run.call_count == 1  # 폴백 없음
    assert r.hit_limit_again


def test_hit_limit_again_판정():
    assert ResumeResult(False, False, "You've hit your usage limit", "", 1.0).hit_limit_again
    assert not ResumeResult(True, False, "다 끝냈습니다", "", 1.0).hit_limit_again
