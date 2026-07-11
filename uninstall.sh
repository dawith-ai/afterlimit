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

echo "완료."
