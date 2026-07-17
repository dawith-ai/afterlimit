"""웹훅 알림 테스트 — 페이로드 형식과 실패 격리."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from afterlimit.config import Config
from afterlimit.notify import _payload, notify


def test_슬랙은_text_필드():
    assert _payload("https://hooks.slack.com/x", "안녕") == {"text": "안녕"}


def test_디스코드는_content_필드():
    assert _payload("https://discord.com/api/webhooks/x", "안녕") == {"content": "안녕"}


def test_알_수_없는_엔드포인트는_두_필드_모두():
    p = _payload("https://example.com/hook", "안녕")
    assert p["text"] == "안녕" and p["content"] == "안녕"


def test_웹훅이_없으면_보내지_않는다():
    assert notify(Config(webhook_url=None), "안녕") is False


def test_성공하면_True():
    cfg = Config(webhook_url="https://hooks.slack.com/x")
    resp = MagicMock()
    resp.status = 200
    resp.__enter__ = lambda s: resp
    resp.__exit__ = lambda *a: False
    with patch("urllib.request.urlopen", return_value=resp) as m:
        assert notify(cfg, "안녕") is True
        sent = json.loads(m.call_args[0][0].data)
        assert sent == {"text": "안녕"}


def test_네트워크_오류는_삼킨다_재개를_막지_않는다():
    """알림 실패가 예외로 새어나가면 재개 사이클 전체가 죽는다. 절대 안 된다."""
    import urllib.error

    cfg = Config(webhook_url="https://hooks.slack.com/x")
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
        assert notify(cfg, "안녕") is False  # 예외 없이 False
