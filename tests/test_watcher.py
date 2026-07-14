"""tmux_resume_watcher 핵심 로직 테스트 (pytest, 표준라이브러리만).

가장 까다로운 순수 함수 _parse_reset(리셋시각 파싱: 시간만/월일/자정넘김/이미지남)과
현지화·시그니처 상수를 검증한다. tmux/네트워크 없이 결정적으로 돈다.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import tmux_resume_watcher as w  # noqa: E402

KST = timezone(timedelta(hours=9))


def test_parse_reset_time_only_future():
    # 22:00에 "resets 11pm" → 오늘 23:00 (가장 가까운 미래)
    now = datetime(2026, 7, 14, 22, 0, tzinfo=KST)
    r = w._parse_reset("5-hour limit reached · resets 11pm", now)
    assert r is not None and r.hour == 23 and r.date() == now.date()


def test_parse_reset_crosses_midnight():
    # 23:00에 "resets 1am" → 내일 01:00 (오늘 01:00은 이미 한참 지남)
    now = datetime(2026, 7, 14, 23, 0, tzinfo=KST)
    r = w._parse_reset("resets 1am", now)
    assert r is not None and r.hour == 1 and r.date() == (now + timedelta(days=1)).date()


def test_parse_reset_just_passed_today():
    # 07:00에 "resets 1:20am" → 오늘 01:20 (이미 지남 = 재개 가능)
    now = datetime(2026, 7, 14, 7, 0, tzinfo=KST)
    r = w._parse_reset("You've hit your session limit · resets 1:20am (Asia/Seoul)", now)
    assert r is not None and r.hour == 1 and r.minute == 20 and r.date() == now.date()


def test_parse_reset_weekly_month_format():
    # 명시 날짜: "resets Jul 18 at 11pm"
    now = datetime(2026, 7, 14, 12, 0, tzinfo=KST)
    r = w._parse_reset("resets Jul 18 at 11pm", now)
    assert r is not None and r.month == 7 and r.day == 18 and r.hour == 23


def test_parse_reset_none_when_absent():
    assert w._parse_reset("nothing relevant here", datetime.now(KST)) is None


def test_localized_continue_map():
    assert w.LOCALIZED_CONTINUE["ko"] == "계속 진행해줘"
    assert w.LOCALIZED_CONTINUE["en"] == "continue"
    assert w.LOCALIZED_CONTINUE["ja"] and w.LOCALIZED_CONTINUE["zh"]


def test_inline_signs_exclude_weekly():
    # 주간 한도는 자동재개 불가 → 트리거에서 제외돼야 함
    assert not any("weekly" in s for s in w.INLINE_SIGNS)


def test_menu_signs_are_three():
    # 인터랙티브 메뉴는 세 문구 모두 있어야 발동
    assert len(w.MENU_SIGNS) == 3
    assert "stop and wait for" in w.MENU_SIGNS


if __name__ == "__main__":  # pytest 없이도 실행 가능
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in fns:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
        except Exception:
            fails += 1
            print(f"  ❌ {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - fails}/{len(fns)} passed")
    sys.exit(1 if fails else 0)
