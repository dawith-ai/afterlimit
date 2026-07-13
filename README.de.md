# Dawith Claude terminal auto (Mac)

> **Nie wieder Claude Code beim Nutzungslimit babysitten.** Wenn Claude Code sein Nutzungslimit erreicht, wählt dieses macOS-Hintergrundtool automatisch **"Stop and wait for limit to reset"** — so setzt sich deine Arbeit von selbst fort, sobald das Limit zurückgesetzt wird, selbst wenn du gerade weg bist.

[English](README.md) · [한국어](README.ko.md) · [中文](README.zh.md) · [日本語](README.ja.md) · [Español](README.es.md) · [Français](README.fr.md) · **Deutsch** · [Português](README.pt.md) · [Русский](README.ru.md)

![Platform](https://img.shields.io/badge/platform-macOS-black) ![Python](https://img.shields.io/badge/python-3-blue) ![License](https://img.shields.io/badge/license-MIT-green)

```
What do you want to do?
❯ 1. Stop and wait for limit to reset
  2. Upgrade your plan
```

Kein Sitzenbleiben mehr am Terminal, um in diesem Menü **"1"** zu drücken. Geh ruhig weg — es setzt sich beim Zurücksetzen fort und pingt dich dann über **Discord / Telegram / Slack** an, damit du weißt, dass es weitergelaufen ist.

**Der komplette Ablauf**: Gib die Git-URL an Claude Code → `install.sh` → der Befehl `/continue` wird installiert → du führst `/continue` aus und gehst weg → beim Token-Reset setzt sich deine Arbeit automatisch fort → du bekommst eine Messenger-Benachrichtigung.

## Grundidee: Beobachten kostet null Tokens

Der Watcher ist **lokales Python, das nur deinen Bildschirm und deine Dateien scannt** — er ruft Claude nie auf und verbraucht daher null Tokens (= null Kosten). Tokens werden nur in dem Moment ausgegeben, in dem die Arbeit **tatsächlich fortgesetzt** wird, und nur einmal pro Sitzung beim Reset. "Ist es nicht teuer, jede Minute zu laufen?" → Das Abfragen ist gratis; nur das Fortsetzen kostet.

## Zwei Sicherheitsnetze

| Name | Target | Action | Interval |
|---|---|---|---|
| **tmux-resume** (core) | live terminal sessions inside tmux | verarbeitet **beide Limit-Formen** (Menü / Inline), liest die exakte Reset-Zeit aus der Usage-API und **tippt beim Reset `continue`**, um die Arbeit tatsächlich fortzusetzen — Ende-zu-Ende verifiziert | 60s |
| **resume-safety** (backup) | parked sessions you walked away from | scans conversation logs (jsonl) → `claude --resume` in the background at reset | 300s |

Sie überschneiden sich nicht: `resume-safety` **weicht zurück** bei Projekten, die eine aktive Sitzung haben (um nicht um dasselbe Konto-Kontingent zu konkurrieren), und dieses Terminal wird von `tmux-resume` übernommen.

## Modus: wie weit es automatisch fortfährt

Du wählst über `resume_mode` in `~/.config/claude-terminal-auto/notify.json`:

| Mode | Behavior | Tokens |
|---|---|---|
| **`token_only`** (default, recommended) | handles only the usage-limit menu. Stops and asks when a task finishes. | frugal |
| **`keep_going`** | the above, plus **auto-nudges idle sessions to keep going** after they finish → never stops overnight. | keeps spending |

```jsonc
{ "resume_mode": "token_only" }   // or "keep_going"
```

`keep_going`-Schutzmechanismen: rührt nie eine Sitzung an, die gerade generiert oder einen Entwurf im Eingabefeld hat; 15 Minuten Abklingzeit pro Pane. (Es arbeitet weiterhin autonom und verbraucht Tokens, also aktiviere es nur, wenn du das möchtest.)

## Voraussetzungen

- **macOS** (launchd)
- **tmux** — Sitzungen müssen innerhalb von tmux laufen, damit Tasten eingeschleust werden können (macOS blockiert das Einschleusen von Tasten außerhalb von tmux)
- **Python 3** — nur die Standardbibliothek, nichts zu installieren

## Installation

```bash
git clone https://github.com/dawith-ai/Dawith-Claude-terminal-auto-Mac.git
cd Dawith-Claude-terminal-auto-Mac
./install.sh
```

`install.sh` schreibt die Plist-Pfade auf diesen Ordner um, installiert sie nach `~/Library/LaunchAgents` und registriert sie bei launchd (übersteht einen Neustart). Wenn du Claude Code verwendest, wird auch der **Slash-Befehl `/continue`** (und seine Übersetzungen) nach `~/.claude/commands/` installiert. Status prüfen:

```bash
launchctl list | grep claude-terminal-auto
```

## Deinstallation

```bash
./uninstall.sh
```

## Der Slash-Befehl `/continue` (Claude Code)

Getrennt vom Hintergrund-Watcher löst das Tippen von **`/continue`** in Claude Code das Sicherheitsnetz einmalig aus und setzt unterbrochene Arbeit fort.

- **Hintergrund-Automatik** (launchd) = automatisch beim Reset, kein Tippen nötig
- **`/continue`** (Slash) = ein manueller Auslöser für den Fall, dass du sofort starten möchtest, statt zu warten

Lokalisierte Befehlsnamen sind für jede Sprache installiert, sodass du ihn in deiner verwenden kannst:

| Language | Command | Language | Command |
|---|---|---|---|
| English | `/continue` | Español | `/continuar` |
| 한국어 | `/지속` | Français | `/continuer` |
| 中文 | `/继续` | Deutsch | `/weiter` |
| 日本語 | `/続行` | Português | `/prosseguir` |
| Русский | `/продолжить` | | |

## Messenger-Benachrichtigungen (Discord / Telegram / Slack / beliebiger Webhook)

Erhalte einen Ping, wenn die Arbeit automatisch fortgesetzt wird. `install.sh` erstellt eine Vorlage unter `~/.config/claude-terminal-auto/notify.json`; **trage nur die Kanäle ein, die du möchtest** (lass den Rest leer, um sie aus zu lassen).

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

- **Weitere Messenger hinzufügen, auf zwei Arten**:
  1. **Ohne Code** — füge `{url, field, name}` zu `generic_webhooks` hinzu (funktioniert mit Mattermost, Google Chat, Slack-kompatiblen und den meisten Diensten, die einen JSON-POST akzeptieren)
  2. **Dedizierte Funktion** — füge eine `_send_*`-Funktion in `scripts/notify.py` und eine Zeile zu `_SENDERS` hinzu (für spezielle Formate)
- Auch über Umgebungsvariablen konfigurierbar: `CLAUDE_AUTO_DISCORD_WEBHOOK` / `CLAUDE_AUTO_TELEGRAM_TOKEN` / `CLAUDE_AUTO_TELEGRAM_CHAT_ID` / `CLAUDE_AUTO_SLACK_WEBHOOK`
- Test: `python3 scripts/notify.py "test"` → sendet an die konfigurierten Kanäle
- ⚠️ `notify.json` enthält Tokens/Webhooks und wird daher von Git ignoriert (das Repo liefert nur eine leere `notify.example.json`).

## Funktionsweise

- **tmux-resume** (`scripts/tmux_resume_watcher.py`): liest jedes Pane mit `tmux capture-pane` und führt einen **2-stufigen** Ablauf über **beide Limit-Formen** aus — das interaktive Menü (`What do you want to do?`) und die Inline-Meldung (`You've hit your session limit · resets 3pm`). Warum zwei Stufen: Die Auswahl von "Stop and wait for limit to reset" setzt die Arbeit **nicht** von selbst fort — Claude Code bleibt beim Reset untätig, bis du `continue` tippst (ein bekanntes offenes Problem, [#18980](https://github.com/anthropics/claude-code/issues/18980) / [#35744](https://github.com/anthropics/claude-code/issues/35744)). Also:
  1. **Beim Limit** — beim Menü drückt es **`1` → `Enter`** (niemals `Esc`, was das Menü abbricht); es liest die **exakte Reset-Zeit aus der Usage-API** (`GET /api/oauth/usage` → `five_hour.resets_at`) und greift ansonsten auf das Parsen des Bildschirms zurück.
  2. **Zur Reset-Zeit** — es tippt **`continue`** (konfigurierbar über `continue_prompt`), um die unterbrochene Arbeit tatsächlich fortzusetzen.

  Wenn eine Sitzung bestätigt, dass sie fortgesetzt wurde, wird der komplette Zyklus (Limit erkannt → `continue` gesendet → Arbeit fortgesetzt) in `/tmp/openclaw_tmux_resume_PROOF.log` aufgezeichnet und an deinen Messenger gesendet. **Schutzmechanismen**: löst nur aus, wenn eine Limit-Form vorhanden ist und das Pane untätig ist (nicht die normale Eingabeleiste `bypass permissions` / der Generierungszustand `esc to interrupt`).
- **resume-safety** (`scripts/resume_blocked_sessions.py`): durchsucht Konversationsprotokolle unter `~/.claude/projects`, findet durch das Limit blockierte Sitzungen und setzt sie beim Reset mit einem frischen `claude --resume`-Prozess fort. Es hat **Sparsamkeits-Schutzmechanismen** (ein Resume pro 5-Stunden-Sitzungsfenster, ein Schwellenwert für den Sitzungsverbrauch, ab dem zurückgewichen wird). Für Headless-Läufe liest es das Claude-OAuth-Token zur Laufzeit aus dem macOS-Schlüsselbund — nie im Quellcode gespeichert.

## Hinweise

- Dieses Tool geht von **autonomer Ausführung** ("ohne Nachfrage fortfahren") aus, wenn es das Menü bestätigt. Behalte das bei sensibler Arbeit im Hinterkopf.
- Wenn du dieselben Skripte bereits unter einem anderen Label ausführst (z. B. `com.openclaw.*`), **installiere nicht doppelt** (vermeidet doppeltes Auslösen).

## Lizenz

MIT
