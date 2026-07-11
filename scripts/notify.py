#!/usr/bin/env python3
"""메신저 알림 (Discord / Telegram / Slack) — 표준 라이브러리만 사용.

설정 우선순위: 환경변수 > 설정파일(~/.config/claude-terminal-auto/notify.json).
아무 채널도 설정 안 되어 있으면 조용히 no-op (에러 없음).

설정 키 (notify.json) / 대응 환경변수:
  discord_webhook   / CLAUDE_AUTO_DISCORD_WEBHOOK    — Discord Incoming Webhook URL
  telegram_token    / CLAUDE_AUTO_TELEGRAM_TOKEN     — Telegram 봇 토큰
  telegram_chat_id  / CLAUDE_AUTO_TELEGRAM_CHAT_ID   — Telegram chat id
  slack_webhook     / CLAUDE_AUTO_SLACK_WEBHOOK      — Slack Incoming Webhook URL
  generic_webhooks  (파일 전용·리스트)               — 임의 서비스: [{"url":..,"field":"text","name":..}]

메신저 추가 방법 2가지:
  1) 코드 없이: notify.json 의 generic_webhooks 에 {url, field, name} 추가
     (Mattermost / Google Chat / Slack 호환 등 JSON POST 받는 서비스 대부분).
  2) 전용 전송함수: _send_* 만들고 _SENDERS 에 한 줄 추가 (형식이 특수할 때, 예: Telegram).

단독 실행: python3 notify.py "테스트 메시지"  → 설정된 채널로 발송 후 결과 출력.
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

CONFIG_PATH = Path(
    os.environ.get(
        "CLAUDE_AUTO_NOTIFY_CONFIG",
        str(Path.home() / ".config" / "claude-terminal-auto" / "notify.json"),
    )
)
MAX_LEN = 1800  # Discord 2000자 제한 등 감안


def _file_config() -> dict:
    try:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _config() -> dict:
    f = _file_config()

    def pick(env_key: str, file_key: str) -> str:
        return (os.environ.get(env_key) or f.get(file_key) or "").strip()

    generic = f.get("generic_webhooks") or []
    if not isinstance(generic, list):
        generic = []
    return {
        "discord_webhook": pick("CLAUDE_AUTO_DISCORD_WEBHOOK", "discord_webhook"),
        "telegram_token": pick("CLAUDE_AUTO_TELEGRAM_TOKEN", "telegram_token"),
        "telegram_chat_id": pick("CLAUDE_AUTO_TELEGRAM_CHAT_ID", "telegram_chat_id"),
        "slack_webhook": pick("CLAUDE_AUTO_SLACK_WEBHOOK", "slack_webhook"),
        "generic_webhooks": generic,
    }


def _post(url: str, payload: dict) -> bool:
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "User-Agent": "claude-terminal-auto",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def _send_discord(cfg: dict, msg: str) -> bool:
    if not cfg["discord_webhook"]:
        return False
    return _post(cfg["discord_webhook"], {"content": msg})


def _send_telegram(cfg: dict, msg: str) -> bool:
    if not (cfg["telegram_token"] and cfg["telegram_chat_id"]):
        return False
    url = f"https://api.telegram.org/bot{cfg['telegram_token']}/sendMessage"
    return _post(url, {"chat_id": cfg["telegram_chat_id"], "text": msg})


def _send_slack(cfg: dict, msg: str) -> bool:
    if not cfg["slack_webhook"]:
        return False
    return _post(cfg["slack_webhook"], {"text": msg})


_SENDERS = [
    ("discord", _send_discord),
    ("telegram", _send_telegram),
    ("slack", _send_slack),
]


def notify(message: str) -> list[str]:
    """설정된 모든 메신저로 message 전송. 성공한 채널명 리스트 반환 (미설정이면 [])."""
    cfg = _config()
    msg = message[:MAX_LEN]
    sent: list[str] = []
    for name, sender in _SENDERS:
        try:
            if sender(cfg, msg):
                sent.append(name)
        except Exception:
            pass
    # 임의 웹훅 — 코드 없이 설정만으로 추가하는 다양한 메신저 (Mattermost/Google Chat/Slack호환 등)
    for i, wh in enumerate(cfg["generic_webhooks"]):
        try:
            url = (wh or {}).get("url", "")
            field = (wh or {}).get("field", "text")
            if url and _post(url, {field: msg}):
                sent.append((wh or {}).get("name") or f"webhook{i + 1}")
        except Exception:
            pass
    return sent


if __name__ == "__main__":
    import sys

    text = " ".join(sys.argv[1:]) or "claude-terminal-auto 테스트 알림 ✅"
    result = notify(text)
    print("전송:", ", ".join(result) if result else "설정된 채널 없음 (notify.json 확인)")
