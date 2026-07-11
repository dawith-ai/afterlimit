# Dawith Claude terminal auto (Mac)

Claude Code 세션이 사용량 한도(usage limit)에 걸려 멈췄을 때, **리셋 시각이 되면 자동으로 "계속"을 입력해 작업을 이어가는** macOS 백그라운드 도구.

터미널 앞에 앉아 파란 **"1번(계속)"을 직접 누르고 기다릴 필요가 없습니다.** 자리를 비워도 리셋 시각에 알아서 이어집니다.

## 핵심: 감시는 토큰 0

감시 자체는 **로컬 파이썬이 화면/파일만 스캔**하므로 Claude 토큰(=요금)을 전혀 쓰지 않습니다. 토큰은 오직 **"실제로 이어가는 발사"** 순간에만, 그것도 리셋 도달 시 세션당 1회씩 나갑니다. "1분마다 도니까 비싸지 않나?" → 폴링은 공짜, 발사만 비용.

## 두 가지 안전망

| 이름 | 대상 | 동작 | 주기 |
|---|---|---|---|
| **tmux-resume** (핵심) | tmux 안의 살아있는 터미널 세션 | 한도 화면 감지 → 리셋 도달 시 그 터미널에 "계속" 자동 입력 | 60초 |
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

## 동작 원리

- **tmux-resume** (`scripts/tmux_resume_watcher.py`): `tmux capture-pane`로 모든 pane 화면을 읽어 한도 메시지(`session limit · resets HH:MM`)를 찾고, 리셋 시각이 지났으면 `tmux send-keys`로 **Escape 2회(멈춤 해제) → "계속 이어서 진행해줘" 입력 → Enter**. pane당 10분 쿨다운으로 중복 입력을 막습니다.
- **resume-safety** (`scripts/resume_blocked_sessions.py`): `~/.claude/projects`의 대화 로그를 스캔해 한도로 막힌 세션을 찾고, 리셋 시각에 새 `claude --resume` 프로세스로 이어갑니다. 세션당 1회(5시간 쿨다운)·세션 사용률 양보 임계 등 **절약 가드**가 들어 있습니다. (headless 실행을 위해 macOS 키체인의 Claude OAuth 토큰을 런타임에 읽습니다 — 소스에 저장하지 않습니다.)

## 주의

- 이 도구는 **자율 실행**("확인 질문 없이 진행")을 전제로 "계속"을 입력합니다. 민감한 작업 중이면 감안하세요.
- 이미 다른 라벨(예: `com.openclaw.*`)로 같은 스크립트를 돌리고 있다면 **중복 설치하지 마세요**(이중 발사 방지).

## 라이선스

MIT
