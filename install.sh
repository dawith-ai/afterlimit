#!/usr/bin/env bash
# AfterLimit 설치 — macOS(launchd) 와 Linux(systemd) 를 모두 지원한다.
#
#   ./install.sh             설치 (5분마다 실행)
#   ./install.sh --uninstall 제거
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/afterlimit"
BIN_DIR="$HOME/.local/bin"
LAUNCH_AGENT="$HOME/Library/LaunchAgents/io.afterlimit.run.plist"
SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

die()  { echo "오류: $*" >&2; exit 1; }
info() { echo "  $*"; }

detect_os() {
  case "$(uname -s)" in
    Darwin) echo macos ;;
    Linux)  echo linux ;;
    *) die "지원하지 않는 OS: $(uname -s) (macOS 와 Linux 만 지원합니다)" ;;
  esac
}

uninstall() {
  case "$(detect_os)" in
    macos)
      launchctl bootout "gui/$(id -u)/io.afterlimit.run" 2>/dev/null || true
      rm -f "$LAUNCH_AGENT"
      info "launchd 에이전트를 제거했습니다."
      ;;
    linux)
      systemctl --user disable --now afterlimit.timer 2>/dev/null || true
      rm -f "$SYSTEMD_DIR/afterlimit.service" "$SYSTEMD_DIR/afterlimit.timer"
      systemctl --user daemon-reload 2>/dev/null || true
      info "systemd 타이머를 제거했습니다."
      ;;
  esac
  rm -f "$BIN_DIR/afterlimit"
  for cmd in "$REPO_DIR"/commands/*.md; do
    [[ -e "$cmd" ]] && rm -f "$HOME/.claude/commands/$(basename "$cmd")"
  done
  echo
  echo "제거했습니다. 상태 파일은 남아 있습니다: $STATE_DIR"
  echo "완전히 지우려면: rm -rf $STATE_DIR"
}

install_bin() {
  command -v python3 >/dev/null || die "python3 가 필요합니다."
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    || die "Python 3.11 이상이 필요합니다."
  command -v claude >/dev/null || info "경고: claude 를 PATH 에서 찾지 못했습니다. 설치는 계속합니다."

  mkdir -p "$BIN_DIR" "$STATE_DIR"
  cat > "$BIN_DIR/afterlimit" <<EOF
#!/usr/bin/env bash
exec python3 -m afterlimit.cli "\$@"
EOF
  chmod +x "$BIN_DIR/afterlimit"

  # pip 없이도 import 되도록 이 저장소를 사용자 site-packages 경로에 등록한다
  python3 - "$REPO_DIR" <<'PY'
import pathlib, site, sys
target = pathlib.Path(site.getusersitepackages())
target.mkdir(parents=True, exist_ok=True)
(target / "afterlimit.pth").write_text(sys.argv[1] + "\n")
PY
  info "afterlimit 을 $BIN_DIR 에 설치했습니다."
}

install_slash_commands() {
  # Claude Code 사용자용 슬래시 명령(/continue, /지속 …). 없으면 조용히 넘어간다.
  local cmd_dir="$HOME/.claude/commands"
  [[ -d "$REPO_DIR/commands" ]] || return 0
  mkdir -p "$cmd_dir"
  local n=0
  for cmd in "$REPO_DIR"/commands/*.md; do
    [[ -e "$cmd" ]] || continue
    cp "$cmd" "$cmd_dir/"
    n=$((n + 1))
  done
  info "슬래시 명령 ${n}개를 $cmd_dir 에 설치했습니다."
}

install_macos() {
  mkdir -p "$(dirname "$LAUNCH_AGENT")"
  sed -e "s|__AFTERLIMIT_BIN__|$BIN_DIR/afterlimit|g" \
      -e "s|__STATE_DIR__|$STATE_DIR|g" \
      -e "s|__PATH__|$BIN_DIR:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin|g" \
      "$REPO_DIR/packaging/launchd/io.afterlimit.run.plist" > "$LAUNCH_AGENT"

  launchctl bootout "gui/$(id -u)/io.afterlimit.run" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$LAUNCH_AGENT"
  info "launchd 에 등록했습니다 (5분 간격)."
}

install_linux() {
  command -v systemctl >/dev/null \
    || die "systemd 가 없습니다. cron 에 다음을 등록하세요: */5 * * * * $BIN_DIR/afterlimit run"
  mkdir -p "$SYSTEMD_DIR"
  cp "$REPO_DIR/packaging/systemd/afterlimit.service" "$SYSTEMD_DIR/"
  cp "$REPO_DIR/packaging/systemd/afterlimit.timer"   "$SYSTEMD_DIR/"
  systemctl --user daemon-reload
  systemctl --user enable --now afterlimit.timer
  info "systemd 타이머를 등록했습니다 (5분 간격)."
  # 로그아웃 후에도 타이머가 돌게 한다. 권한이 없으면 안내만 한다.
  loginctl enable-linger "$USER" 2>/dev/null \
    || info "참고: 'loginctl enable-linger $USER' 를 실행하면 로그아웃 후에도 동작합니다."
}

main() {
  [[ "${1:-}" == "--uninstall" ]] && { uninstall; exit 0; }

  local os; os="$(detect_os)"
  echo "AfterLimit 설치 ($os)"
  install_bin
  install_slash_commands
  case "$os" in
    macos) install_macos ;;
    linux) install_linux ;;
  esac

  echo
  echo "완료했습니다. 확인해 보세요:"
  echo "  afterlimit scan     막힌 세션과 해제 시각"
  echo "  afterlimit config   현재 설정"
  echo
  echo "알림을 받으려면 웹훅 URL 을 넣으세요 (Discord·Slack 등):"
  echo "  export AFTERLIMIT_WEBHOOK_URL='https://...'"
}

main "$@"
