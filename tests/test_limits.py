"""limits.parse_limit 테스트.

핵심 관심사는 **시간대**다. Claude 가 보여주는 reset 시각에는 시간대가 없고,
사용자 로컬 벽시계 시각으로 찍힌다. 같은 "resets 11pm" 이라도 서울 사용자와
뉴욕 사용자에게는 서로 다른 절대 시각이다. 시간대를 상수로 박아두면 한국 밖
사용자는 전부 틀린 시각에 재개된다 — 이 테스트가 그 회귀를 막는다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from afterlimit.limits import SPEND_RETRY, LimitInfo, local_tz, parse_limit

SEOUL = ZoneInfo("Asia/Seoul")
NEW_YORK = ZoneInfo("America/New_York")
UTC = timezone.utc

LIMIT_MSG = "You've hit your usage limit · resets 11pm"


# ── 시간대 ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tz", [SEOUL, NEW_YORK, UTC, ZoneInfo("Europe/Berlin")])
def test_reset_는_anchor_의_시간대로_해석된다(tz):
    """같은 문자열이라도 사용자의 시간대에 따라 다른 절대 시각이어야 한다."""
    anchor = datetime(2026, 7, 17, 14, 0, tzinfo=tz)
    info = parse_limit(LIMIT_MSG, anchor=anchor, now=anchor)

    assert info is not None
    assert info.reset_at == datetime(2026, 7, 17, 23, 0, tzinfo=tz)
    assert info.reset_at.tzinfo is tz  # 시간대가 갈아치워지지 않는다


def test_뉴욕과_서울은_같은_문자열에_다른_절대시각을_준다():
    """KST 를 상수로 박으면 이 테스트가 깨진다 — 국제 사용자 회귀 방지."""
    seoul = parse_limit(
        LIMIT_MSG,
        anchor=datetime(2026, 7, 17, 14, 0, tzinfo=SEOUL),
        now=datetime(2026, 7, 17, 14, 0, tzinfo=SEOUL),
    )
    ny = parse_limit(
        LIMIT_MSG,
        anchor=datetime(2026, 7, 17, 14, 0, tzinfo=NEW_YORK),
        now=datetime(2026, 7, 17, 14, 0, tzinfo=NEW_YORK),
    )

    assert seoul.reset_at.utctimetuple() != ny.reset_at.utctimetuple()
    # 뉴욕 23시는 서울 23시보다 13시간 늦다 (여름, EDT 기준)
    delta = ny.reset_at.astimezone(UTC) - seoul.reset_at.astimezone(UTC)
    assert delta == timedelta(hours=13)


def test_naive_anchor_는_거부한다():
    """시간대를 추측하면 조용히 틀린다. 차라리 터뜨린다."""
    with pytest.raises(ValueError, match="tz-aware"):
        parse_limit(LIMIT_MSG, anchor=datetime(2026, 7, 17, 14, 0))


def test_local_tz_는_시간대를_돌려준다():
    assert local_tz() is not None
    assert datetime.now(local_tz()).tzinfo is not None


# ── 시각 해석 ───────────────────────────────────────────────────────────

def test_자정을_넘기면_다음날로_넘어간다():
    # 밤 11시 30분에 한도 → "resets 1am" 은 오늘이 아니라 내일 1시
    anchor = datetime(2026, 7, 17, 23, 30, tzinfo=SEOUL)
    info = parse_limit("usage limit · resets 1am", anchor=anchor, now=anchor)
    assert info.reset_at == datetime(2026, 7, 18, 1, 0, tzinfo=SEOUL)


def test_분까지_있는_형식():
    anchor = datetime(2026, 7, 17, 14, 0, tzinfo=SEOUL)
    info = parse_limit("usage limit · resets 5:50pm", anchor=anchor, now=anchor)
    assert info.reset_at == datetime(2026, 7, 17, 17, 50, tzinfo=SEOUL)


@pytest.mark.parametrize(
    "text,expected_hour",
    [
        ("usage limit · resets 12am", 0),  # 자정
        ("usage limit · resets 12pm", 12),  # 정오
        ("usage limit · resets 1pm", 13),
    ],
)
def test_12시간제_경계(text, expected_hour):
    anchor = datetime(2026, 7, 17, 0, 0, tzinfo=SEOUL)
    info = parse_limit(text, anchor=anchor, now=anchor)
    assert info.reset_at.hour == expected_hour


def test_주간한도는_명시된_월일을_쓴다():
    anchor = datetime(2026, 6, 4, 10, 0, tzinfo=SEOUL)
    info = parse_limit("weekly limit · resets Jun 6 at 11pm", anchor=anchor, now=anchor)
    assert info.reset_at == datetime(2026, 6, 6, 23, 0, tzinfo=SEOUL)


def test_연말을_넘기면_다음해로_본다():
    anchor = datetime(2026, 12, 28, 10, 0, tzinfo=SEOUL)
    info = parse_limit("weekly limit · resets Jan 2 at 11pm", anchor=anchor, now=anchor)
    assert info.reset_at.year == 2027


def test_anchor_가_너무_오래되면_현재_기준으로_다시_잡는다():
    # 3일 전 세션 — 그때 기준 reset 은 이미 한참 지났다. 지금 기준으로 재추정해야 한다.
    anchor = datetime(2026, 7, 14, 14, 0, tzinfo=SEOUL)
    now = datetime(2026, 7, 17, 14, 0, tzinfo=SEOUL)
    info = parse_limit(LIMIT_MSG, anchor=anchor, now=now)
    assert info.reset_at == datetime(2026, 7, 17, 23, 0, tzinfo=SEOUL)


# ── 한도 종류 ───────────────────────────────────────────────────────────

def test_한도가_아니면_None():
    anchor = datetime(2026, 7, 17, 14, 0, tzinfo=SEOUL)
    assert parse_limit("just a normal error", anchor=anchor) is None
    assert parse_limit("", anchor=anchor) is None


def test_서버측_rate_limit_은_짧게_재시도한다():
    anchor = now = datetime(2026, 7, 17, 14, 0, tzinfo=SEOUL)
    info = parse_limit(
        "This is not your usage limit — we're temporarily limiting requests",
        anchor=anchor,
        now=now,
    )
    assert info.kind == "server_rate"
    assert info.reset_at == now + timedelta(minutes=10)


def test_지출한도는_anchor_기준이라_밀리지_않는다():
    """now 기준으로 잡으면 스캔할 때마다 미래로 밀려 영영 재개되지 않는다."""
    anchor = datetime(2026, 7, 17, 14, 0, tzinfo=SEOUL)
    first = parse_limit("monthly spend limit reached", anchor=anchor, now=anchor)
    later = parse_limit(
        "monthly spend limit reached", anchor=anchor, now=anchor + timedelta(hours=2)
    )

    assert first.kind == "spend"
    assert first.reset_at == anchor + SPEND_RETRY
    assert later.reset_at == first.reset_at  # 시간이 흘러도 고정


def test_reset_시각이_없으면_모른다고_한다():
    anchor = datetime(2026, 7, 17, 14, 0, tzinfo=SEOUL)
    info = parse_limit("You've hit your session limit", anchor=anchor)
    assert info.kind == "usage"
    assert info.reset_at is None


# ── 재개 판단 ───────────────────────────────────────────────────────────

def test_is_over():
    reset = datetime(2026, 7, 17, 23, 0, tzinfo=SEOUL)
    info = LimitInfo("usage", reset)
    assert not info.is_over(reset - timedelta(seconds=1))
    assert info.is_over(reset)
    assert info.is_over(reset + timedelta(hours=1))


def test_reset_을_모르면_재개하지_않는다():
    """모르면 가만히 있는다 — 함부로 두드리지 않는다."""
    assert not LimitInfo("usage", None).is_over(datetime.now(SEOUL))
