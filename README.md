# Dawith Claude terminal auto (Mac)

Claude Code 세션이 사용량 한도(usage limit)에 걸리면 뜨는 **"Stop and wait for limit to reset" 메뉴를 자동으로 선택**해, 리셋 시각에 작업이 그대로 이어지게 하는 macOS 백그라운드 도구.

```
What do you want to do?
❯ 1. Stop and wait for limit to reset
  2. Upgrade your plan
```

터미널 앞에 앉아 이 메뉴에서 **파란 "1번"을 직접 누르고 기다릴 필요가 없습니다.** 자리를 비워도 리셋 시각에 알아서 이어지고, 이어간 뒤엔 **Discord·Telegram·Slack 등으로 알림**을 보내 자리를 비워도 진행 상황을 압니다.

**전체 흐름**: git URL을 Claude Code에 주기 → `install.sh` → `/지속` 자동 설치 → 사용자가 `/지속` 하고 자리 비움 → 토큰 리셋 시각에 이전 작업 자동 이어감 → 메신저로 알림.

## 핵심: 감시는 토큰 0

감시 자체는 **로컬 파이썬이 화면/파일만 스캔**하므로 Claude 토큰(=요금)을 전혀 쓰지 않습니다. 토큰은 오직 **"실제로 이어가는 발사"** 순간에만, 그것도 리셋 도달 시 세션당 1회씩 나갑니다. "1분마다 도니까 비싸지 않나?" → 폴링은 공짜, 발사만 비용.

## 두 가지 안전망

| 이름 | 대상 | 동작 | 주기 |
|---|---|---|---|
| **tmux-resume** (핵심) | tmux 안의 살아있는 터미널 세션 | 한도 메뉴 감지 → 옵션 1 "Stop and wait for limit to reset" 자동 선택(1 → Enter) | 60초 |
| **resume-safety** (보조) | 자리 뜬 파킹된 세션 | 대화로그(jsonl) 스캔 → 리셋 시각에 백그라운드 `claude --resume` | 300초 |

두 안전망은 **안 겹칩니다**: `resume-safety`는 살아있는 세션이 있는 프로젝트엔 **양보**(같은 계정 quota 경쟁 방지)하고, 그 터미널은 `tmux-resume`가 담당합니다.

## 요구사항

- **macOS** (launchd)
- **tmux** — 터미널 세션이 tmux 안에서 돌아야 자동 입력 가능(macOS는 tmux 밖 세션에 키 주입을 차단합니다)
- **Python 3** — 표준 라이브러리만 사용, 추가 설치 불필요

## 설치

```bash
git clone https://github.com/dawith-ai/Dawith-Claude-terminal-auto-Mac.git
cd Dawith-Claude-terminal-auto-Mac
./install.sh
```

`install.sh`가 plist의 경로를 이 폴더에 맞춰 `~/Library/LaunchAgents`에 설치하고 launchd에 등록합니다(재부팅에도 유지). Claude Code 사용자면 **`/지속` 슬래시 명령**도 `~/.claude/commands/`에 함께 설치됩니다(즉시 1회 재개 트리거). 상태 확인:

```bash
launchctl list | grep claude-terminal-auto
```

## 제거

```bash
./uninstall.sh
```

## `/지속` 슬래시 명령 (Claude Code)

백그라운드 감시기와 별개로, Claude Code에서 **`/지속`을 직접 입력**하면 안전망을 즉시 1회 발사하고 중단된 작업을 이어가게 합니다. `install.sh`가 `~/.claude/commands/지속.md`로 설치합니다.

- **백그라운드 자동**(launchd) = 타이핑 없이 리셋 시각에 자동
- **`/지속`**(슬래시) = 리셋 직후 5분 안 기다리고 즉시 시작하고 싶을 때 수동 트리거

## 메신저 알림 (Discord / Telegram / Slack / 임의 웹훅)

작업이 자동으로 이어질 때 메신저로 알림을 받습니다. `install.sh`가 `~/.config/claude-terminal-auto/notify.json` 템플릿을 만들어두니, **원하는 채널만 채우면** 켜집니다(아무것도 안 채우면 조용히 비활성).

```jsonc
{
  "discord_webhook": "https://discord.com/api/webhooks/...",   // Discord Incoming Webhook
  "telegram_token": "123456:ABC...",                            // Telegram 봇 토큰
  "telegram_chat_id": "12345678",                               // Telegram chat id
  "slack_webhook": "https://hooks.slack.com/services/...",      // Slack Incoming Webhook
  "generic_webhooks": [                                         // 그 외 다양한 메신저 (코드 수정 없이)
    { "name": "mattermost", "url": "https://.../hooks/xxx", "field": "text" }
  ]
}
```

- **다양한 메신저 추가 2가지 방법**:
  1. **코드 없이** — `generic_webhooks`에 `{url, field, name}` 추가 (Mattermost·Google Chat·Slack호환 등 JSON POST 받는 서비스 대부분)
  2. **전용 함수** — `scripts/notify.py`의 `_send_*` 함수 만들고 `_SENDERS`에 한 줄 (형식이 특수할 때)
- 환경변수로도 설정 가능: `CLAUDE_AUTO_DISCORD_WEBHOOK` / `CLAUDE_AUTO_TELEGRAM_TOKEN` / `CLAUDE_AUTO_TELEGRAM_CHAT_ID` / `CLAUDE_AUTO_SLACK_WEBHOOK`
- 테스트: `python3 scripts/notify.py "테스트"` → 설정된 채널로 발송
- ⚠️ `notify.json`은 토큰/웹훅이 담기므로 `.gitignore`로 커밋 제외됨 (레포엔 빈 `notify.example.json`만 포함).

## 동작 원리

- **tmux-resume** (`scripts/tmux_resume_watcher.py`): `tmux capture-pane`로 모든 pane을 읽어 한도 결정 메뉴(`What do you want to do? / 1. Stop and wait for limit to reset / 2. Upgrade your plan`)가 **활성 상태**로 떠 있으면, `tmux send-keys`로 **"1" 선택 → Enter**로 옵션 1을 확정합니다 → Claude가 리셋 시각에 작업을 자동으로 이어감. (⚠️ Esc는 메뉴를 취소하므로 절대 쓰지 않음.) **오탐 차단**: 메뉴 세 문구가 다 있고 일반 입력바(`bypass permissions`)·생성중(`esc to interrupt`)이 아닐 때만 발사, pane당 5분 쿨다운.
- **resume-safety** (`scripts/resume_blocked_sessions.py`): `~/.claude/projects`의 대화 로그를 스캔해 한도로 막힌 세션을 찾고, 리셋 시각에 새 `claude --resume` 프로세스로 이어갑니다. 세션당 1회(5시간 쿨다운)·세션 사용률 양보 임계 등 **절약 가드**가 들어 있습니다. (headless 실행을 위해 macOS 키체인의 Claude OAuth 토큰을 런타임에 읽습니다 — 소스에 저장하지 않습니다.)

## 주의

- 이 도구는 **자율 실행**("확인 질문 없이 진행")을 전제로 "계속"을 입력합니다. 민감한 작업 중이면 감안하세요.
- 이미 다른 라벨(예: `com.openclaw.*`)로 같은 스크립트를 돌리고 있다면 **중복 설치하지 마세요**(이중 발사 방지).

## 라이선스

MIT
