"""알림 — 웹훅 URL 하나만 받는다. 특정 서비스에 묶이지 않는다.

Discord·Slack·그 밖의 무엇이든 JSON 을 받는 URL 이면 된다. 설정이 없으면 조용히 넘어간다.
알림이 실패해도 재개는 계속돼야 하므로 예외를 밖으로 던지지 않는다.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from afterlimit.config import Config

__all__ = ["notify"]

_TIMEOUT_SEC = 10


def _payload(url: str, text: str) -> dict:
    """서비스마다 기대하는 필드 이름이 다르다. URL 로 구분한다."""
    if "slack.com" in url:
        return {"text": text}
    if "discord.com" in url or "discordapp.com" in url:
        return {"content": text}
    return {"text": text, "content": text}  # 알 수 없는 곳 — 흔한 두 이름을 모두 채워 보낸다


def notify(cfg: Config, text: str) -> bool:
    """웹훅으로 한 줄 보낸다. 보냈으면 True. 설정이 없거나 실패하면 False."""
    if not cfg.webhook_url:
        return False
    req = urllib.request.Request(
        cfg.webhook_url,
        data=json.dumps(_payload(cfg.webhook_url, text)).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SEC) as res:
            return 200 <= res.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False  # 알림은 부가 기능이다 — 실패해도 재개를 막지 않는다
