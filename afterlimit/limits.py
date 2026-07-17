"""한도 메시지 파싱 — 순수 함수, 표준 라이브러리만.

Claude Code 세션 기록에 남는 한도 메시지에서 '언제 풀리는지'를 뽑아낸다.
I/O·알림·스케줄러와 분리돼 있어 어디서든 재사용·테스트할 수 있다.

## 시간대에 대하여 (중요)

Claude 가 표시하는 reset 시각("resets 5:50pm")에는 **시간대가 없다.**
사용자의 로컬 시간으로 렌더링된 벽시계 시각일 뿐이다.
따라서 이 문자열을 절대 시각으로 바꾸려면 **한도가 발생한 시점(anchor)의 시간대**를
그대로 써야 한다. 특정 시간대를 상수로 박으면 그 지역 밖에서는 전부 틀린 시각이 나온다.

이 모듈은 시간대를 전역 상수로 두지 않고 `anchor` 에서 가져온다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Literal

__all__ = ["LimitInfo", "LimitKind", "local_tz", "parse_limit"]

LimitKind = Literal["usage", "server_rate", "spend"]

#: 한도 메시지로 볼 표지. 하나라도 없으면 한도가 아니다.
LIMIT_PATTERNS: tuple[str, ...] = (
    "session limit",
    "usage limit",
    "5-hour limit",
    "weekly limit",
    "spend limit",
    "rate limited",
)

#: 서버 측 rate limit — 내 사용량 한도가 아니라서 reset 시각이 없다.
SERVER_RATE_MARKERS: tuple[str, ...] = (
    "not your usage limit",
    "temporarily limiting",
    "rate limited",
)

#: "resets 11pm" (시간만) / "resets Jun 6 at 11pm" (월·일 명시)
RESET_RE = re.compile(
    r"resets?\s+"
    r"(?:(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(\d{1,2})\s+at\s+)?"
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)",
    re.IGNORECASE,
)

_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"),
        start=1,
    )
}

#: 서버 측 rate limit 재시도 간격. reset 시각이 없으니 짧게 잡고 다시 본다.
SERVER_RATE_RETRY = timedelta(minutes=10)
#: 월 지출 한도는 사용자가 직접 올려야 풀린다. 매 사이클 무의미하게 두드리지 않도록 간격을 둔다.
SPEND_RETRY = timedelta(minutes=30)
#: 시간만 있는 reset 을 anchor 로 못 맞추면(기록이 오래됐거나 시계가 어긋남) 현재 기준으로 다시 잡는다.
_ANCHOR_STALE = timedelta(hours=12)


@dataclass(frozen=True)
class LimitInfo:
    """한도 하나. `reset_at` 이 None 이면 언제 풀리는지 알 수 없다는 뜻이다."""

    kind: LimitKind
    reset_at: datetime | None
    #: 매칭된 원문 조각. 로그·디버깅용
    raw: str = ""

    def is_over(self, now: datetime) -> bool:
        """지금 재개해도 되는가. reset 시각을 모르면 재개하지 않는다."""
        return self.reset_at is not None and now >= self.reset_at


def local_tz() -> tzinfo:
    """이 기기의 로컬 시간대. 한국이면 KST, 미국이면 그쪽 시간대가 나온다."""
    tz = datetime.now().astimezone().tzinfo
    assert tz is not None  # astimezone() 은 항상 tzinfo 를 채운다
    return tz


def _resolve_dated(month: str, day: str, hour: int, minute: int, anchor: datetime) -> datetime:
    """'Jun 6 at 11pm' 처럼 월·일이 명시된 경우. 연도는 anchor 에서 추론한다."""
    try:
        reset = anchor.replace(
            month=_MONTHS[month.lower()],
            day=int(day),
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )
    except ValueError:
        # 2월 30일 같은 실재하지 않는 날짜 — 판단 불가
        return anchor + timedelta(hours=1)

    # 연말→연초를 넘어가는 경우. 한참 과거면 내년으로 본다.
    if reset < anchor - timedelta(days=180):
        reset = reset.replace(year=reset.year + 1)
    return reset


def _resolve_time_only(hour: int, minute: int, anchor: datetime, now: datetime) -> datetime:
    """'11pm' 처럼 시간만 있는 경우. 한도가 걸린 시점(anchor) 이후 가장 가까운 그 시각."""
    reset = anchor.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if reset < anchor:
        reset += timedelta(days=1)  # 자정을 넘김

    # anchor 가 너무 오래됐으면 그 추정을 믿을 수 없다. 현재 기준으로 다시 잡는다.
    if reset <= now - _ANCHOR_STALE:
        reset = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if reset < now:
            reset += timedelta(days=1)
    return reset


def parse_limit(text: str, *, anchor: datetime, now: datetime | None = None) -> LimitInfo | None:
    """한도 메시지에서 종류와 해제 시각을 뽑는다. 한도 메시지가 아니면 None.

    Args:
        text: 세션에 기록된 에러 메시지 본문.
        anchor: **한도가 발생한 시점.** tz-aware 여야 한다. reset 시각의 시간대와
            날짜 추론이 전부 여기서 나온다. 보통 세션 파일의 mtime 을 넣는다.
        now: 현재 시각. 생략하면 anchor 의 시간대로 지금을 쓴다. 테스트에서 주입한다.

    Raises:
        ValueError: anchor 가 naive(시간대 없음)인 경우. 시간대를 추측하면
            그 지역 밖에서 조용히 틀린 시각이 나오므로 거부한다.
    """
    if anchor.tzinfo is None:
        raise ValueError("anchor 는 tz-aware 여야 합니다 (시간대를 추측하지 않습니다)")
    if now is None:
        now = datetime.now(anchor.tzinfo)

    lowered = text.lower()
    if not any(p in lowered for p in LIMIT_PATTERNS):
        return None

    # 서버가 건 rate limit — 내 한도가 아니고 reset 시각도 없다. 잠시 뒤 다시 본다.
    if any(m in lowered for m in SERVER_RATE_MARKERS):
        return LimitInfo("server_rate", now + SERVER_RATE_RETRY, "server-side rate limit")

    # 월 지출 한도 — 사용자가 콘솔에서 올려야 풀린다.
    # anchor 기준으로 잡아야 한다. now 기준이면 스캔할 때마다 미래로 밀려 영영 재개되지 않는다.
    if "spend limit" in lowered:
        return LimitInfo("spend", anchor + SPEND_RETRY, "monthly spend limit")

    m = RESET_RE.search(text)
    if not m:
        return LimitInfo("usage", None, "")  # 한도는 맞는데 언제 풀리는지 모름

    month, day, hour_s, minute_s, ampm = m.groups()
    hour = int(hour_s)
    minute = int(minute_s or 0)
    if ampm.lower() == "pm" and hour != 12:
        hour += 12
    elif ampm.lower() == "am" and hour == 12:
        hour = 0

    reset = (
        _resolve_dated(month, day, hour, minute, anchor)
        if month and day
        else _resolve_time_only(hour, minute, anchor, now)
    )
    return LimitInfo("usage", reset, m.group(0))
