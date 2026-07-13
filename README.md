# Dawith Claude terminal auto (Mac)

> **Never babysit Claude Code's usage limit again.** When Claude Code hits its usage limit, this macOS background tool auto-selects **"Stop and wait for limit to reset"** — so your work resumes on its own the moment the limit resets, even while you're away.

**English** · [한국어](README.ko.md) · [中文](README.zh.md) · [日本語](README.ja.md) · [Español](README.es.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · [Português](README.pt.md) · [Русский](README.ru.md)

![Platform](https://img.shields.io/badge/platform-macOS-black) ![Python](https://img.shields.io/badge/python-3-blue) ![License](https://img.shields.io/badge/license-MIT-green)

```
What do you want to do?
❯ 1. Stop and wait for limit to reset
  2. Upgrade your plan
```

No more sitting at your terminal to press **"1"** on this menu. Walk away — it resumes at reset, then pings you on **Discord / Telegram / Slack** so you know it continued.

**The full flow**: hand the git URL to Claude Code → `install.sh` → the `/continue` command is installed → you run `/continue` and leave → at the token reset your work auto-resumes → you get a messenger notification.

## Core idea: watching costs zero tokens

The watcher is **local Python that only scans your screen and files** — it never calls Claude, so it spends zero tokens (= zero cost). Tokens are spent only at the moment work **actually resumes**, and only once per session at reset. "Isn't running every minute expensive?" → polling is free; only the resume costs.

## Two safety nets

| Name | Target | Action | Interval |
|---|---|---|---|
| **tmux-resume** (core) | live terminal sessions inside tmux | handles **both limit forms** (menu / inline), reads the exact reset time from the usage API, and **types `continue` at reset** to actually resume — verified end-to-end | 60s |
| **resume-safety** (backup) | parked sessions you walked away from | scans conversation logs (jsonl) → `claude --resume` in the background at reset | 300s |

They don't overlap: `resume-safety` **yields** on projects that have a live session (to avoid competing for the same account quota), and that terminal is handled by `tmux-resume`.

## Mode: how far it auto-continues

You choose via `resume_mode` in `~/.config/claude-terminal-auto/notify.json`:

| Mode | Behavior | Tokens |
|---|---|---|
| **`token_only`** (default, recommended) | handles only the usage-limit menu. Stops and asks when a task finishes. | frugal |
| **`keep_going`** | the above, plus **auto-nudges idle sessions to keep going** after they finish → never stops overnight. | keeps spending |

```jsonc
{ "resume_mode": "token_only" }   // or "keep_going"
```

`keep_going` safeguards: never touches a session that is generating or has a draft in the input box; 15-minute cooldown per pane. (It keeps working autonomously and spends tokens, so enable it only when you want that.)

## Requirements

- **macOS** (launchd)
- **tmux** — sessions must run inside tmux for key injection (macOS blocks injecting keys outside tmux)
- **Python 3** — standard library only, nothing to install

## Install

```bash
git clone https://github.com/dawith-ai/Dawith-Claude-terminal-auto-Mac.git
cd Dawith-Claude-terminal-auto-Mac
./install.sh
```

`install.sh` rewrites the plist paths to this folder, installs them to `~/Library/LaunchAgents`, and registers them with launchd (survives reboot). If you use Claude Code, the **`/continue` slash command** (and its translations) is installed to `~/.claude/commands/` too. Check status:

```bash
launchctl list | grep claude-terminal-auto
```

## Uninstall

```bash
./uninstall.sh
```

## The `/continue` slash command (Claude Code)

Separate from the background watcher, typing **`/continue`** in Claude Code fires the safety net once and resumes interrupted work.

- **Background auto** (launchd) = automatic at reset, no typing needed
- **`/continue`** (slash) = a manual trigger for when you want to start immediately instead of waiting

Localized command names are installed for every language, so you can use it in yours:

| Language | Command | Language | Command |
|---|---|---|---|
| English | `/continue` | Español | `/continuar` |
| 한국어 | `/지속` | Français | `/continuer` |
| 中文 | `/继续` | Deutsch | `/weiter` |
| 日本語 | `/続行` | Português | `/prosseguir` |
| Русский | `/продолжить` | | |

## Messenger notifications (Discord / Telegram / Slack / any webhook)

Get a ping when work auto-resumes. `install.sh` creates a template at `~/.config/claude-terminal-auto/notify.json`; **fill in only the channels you want** (leave the rest empty to keep them off).

```jsonc
{
  "resume_mode": "token_only",
  "discord_webhook": "https://discord.com/api/webhooks/...",   // Discord Incoming Webhook
  "telegram_token": "123456:ABC...",                            // Telegram bot token
  "telegram_chat_id": "12345678",                               // Telegram chat id
  "slack_webhook": "https://hooks.slack.com/services/...",      // Slack Incoming Webhook
  "generic_webhooks": [                                         // any other messenger, no code needed
    { "name": "mattermost", "url": "https://.../hooks/xxx", "field": "text" }
  ]
}
```

- **Add more messengers, two ways**:
  1. **No code** — add `{url, field, name}` to `generic_webhooks` (works with Mattermost, Google Chat, Slack-compatible, and most services that accept a JSON POST)
  2. **Dedicated function** — add a `_send_*` function in `scripts/notify.py` and one line to `_SENDERS` (for special formats)
- Also configurable via env vars: `CLAUDE_AUTO_DISCORD_WEBHOOK` / `CLAUDE_AUTO_TELEGRAM_TOKEN` / `CLAUDE_AUTO_TELEGRAM_CHAT_ID` / `CLAUDE_AUTO_SLACK_WEBHOOK`
- Test: `python3 scripts/notify.py "test"` → sends to the configured channels
- ⚠️ `notify.json` holds tokens/webhooks, so it is git-ignored (the repo ships only an empty `notify.example.json`).

## How it works

- **tmux-resume** (`scripts/tmux_resume_watcher.py`): reads every pane with `tmux capture-pane` and runs a **2-step** flow across **both limit forms** — the interactive menu (`What do you want to do?`) and the inline message (`You've hit your session limit · resets 3pm`). Why two steps: selecting "Stop and wait for limit to reset" does **not** auto-resume by itself — Claude Code sits idle at reset until you type `continue` (a known open issue, [#18980](https://github.com/anthropics/claude-code/issues/18980) / [#35744](https://github.com/anthropics/claude-code/issues/35744)). So:
  1. **At the limit** — for the menu it presses **`1` → `Enter`** (never `Esc`, which cancels the menu); it reads the **exact reset time from the usage API** (`GET /api/oauth/usage` → `five_hour.resets_at`), falling back to on-screen parsing.
  2. **At the reset time** — it types **`continue`** (configurable via `continue_prompt`) to actually resume the interrupted work.

  When a session confirms it resumed, the full cycle (limit detected → `continue` sent → work resumed) is recorded to `/tmp/openclaw_tmux_resume_PROOF.log` and pushed to your messenger. **Guards**: acts only when a limit form is present and the pane is idle (not the normal input bar `bypass permissions` / generating state `esc to interrupt`).
- **resume-safety** (`scripts/resume_blocked_sessions.py`): scans conversation logs under `~/.claude/projects`, finds limit-blocked sessions, and resumes them with a fresh `claude --resume` process at reset. It has **frugality guards** (one resume per 5-hour session window, a session-usage yield threshold). For headless runs it reads the Claude OAuth token from the macOS keychain at runtime — never stored in source.

## Notes

- This tool assumes **autonomous execution** ("proceed without asking") when it confirms the menu. Keep that in mind during sensitive work.
- If you already run the same scripts under a different label (e.g. `com.openclaw.*`), **do not double-install** (avoids double-firing).

## License

MIT
