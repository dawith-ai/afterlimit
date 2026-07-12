#!/usr/bin/env bash
# claude-terminal-auto 제거: launchd 등록 해제 + plist 삭제.
set -euo pipefail

LA="$HOME/Library/LaunchAgents"
UID_NUM="$(id -u)"

for label in tmux-resume resume-safety; do
  launchctl bootout "gui/$UID_NUM/com.claude-terminal-auto.$label" 2>/dev/null || true
  launchctl disable "gui/$UID_NUM/com.claude-terminal-auto.$label" 2>/dev/null || true
  rm -f "$LA/com.claude-terminal-auto.$label.plist"
  echo "🗑  제거: com.claude-terminal-auto.$label"
done

# 각 언어 슬래시 명령 제거
CMD_DIR="$HOME/.claude/commands"
for c in continue 지속 继续 続行 continuar continuer weiter prosseguir продолжить; do
  rm -f "$CMD_DIR/$c.md"
done
echo "🗑  제거: 각 언어 슬래시 명령"

echo "완료."
