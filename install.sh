#!/usr/bin/env bash
# claude-terminal-auto 설치: plist 경로를 이 폴더로 맞춰 launchd에 등록.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$(command -v python3 || true)"
LA="$HOME/Library/LaunchAgents"
UID_NUM="$(id -u)"

if [ -z "$PYTHON" ]; then echo "❌ python3 를 찾을 수 없습니다"; exit 1; fi
if ! command -v tmux >/dev/null 2>&1; then
  echo "⚠️  tmux 미설치 — tmux-resume는 tmux 세션에만 동작합니다 (brew install tmux)"
fi

mkdir -p "$LA"

for label in tmux-resume resume-safety; do
  src="$REPO_DIR/launchd/com.claude-terminal-auto.$label.plist"
  dst="$LA/com.claude-terminal-auto.$label.plist"
  sed -e "s|__REPO_DIR__|$REPO_DIR|g" \
      -e "s|__PYTHON__|$PYTHON|g" \
      -e "s|__HOME__|$HOME|g" \
      "$src" > "$dst"
  launchctl enable  "gui/$UID_NUM/com.claude-terminal-auto.$label" 2>/dev/null || true
  launchctl bootout "gui/$UID_NUM/com.claude-terminal-auto.$label" 2>/dev/null || true
  launchctl bootstrap "gui/$UID_NUM" "$dst"
  echo "✅ 설치: com.claude-terminal-auto.$label"
done

# 메신저 알림 설정 템플릿 (Discord/Telegram/Slack/임의 웹훅) — 채우면 재개 시 알림
CFG_DIR="$HOME/.config/claude-terminal-auto"
mkdir -p "$CFG_DIR"
if [ ! -f "$CFG_DIR/notify.json" ] && [ -f "$REPO_DIR/notify.example.json" ]; then
  cp "$REPO_DIR/notify.example.json" "$CFG_DIR/notify.json"
  echo "📨 알림 설정 템플릿 생성: $CFG_DIR/notify.json (webhook/토큰 채우면 알림 켜짐)"
fi

# 슬래시 명령 설치 (Claude Code 사용자용 — 각 언어별 /continue /지속 /继续 /続行 …)
CMD_DIR="$HOME/.claude/commands"
if [ -d "$REPO_DIR/commands" ]; then
  mkdir -p "$CMD_DIR"
  n=0
  for cmd in "$REPO_DIR"/commands/*.md; do
    [ -e "$cmd" ] || continue
    sed -e "s|__REPO_DIR__|$REPO_DIR|g" \
        -e "s|__PYTHON__|$PYTHON|g" \
        "$cmd" > "$CMD_DIR/$(basename "$cmd")"
    n=$((n + 1))
  done
  echo "✅ 설치: 각 언어 슬래시 명령 ${n}개 (→ ~/.claude/commands/)"
fi

echo ""
echo "완료. 상태 확인:  launchctl list | grep claude-terminal-auto"
