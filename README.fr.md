# Dawith Claude terminal auto (Mac)

> **Ne surveillez plus jamais la limite d'utilisation de Claude Code.** Lorsque Claude Code atteint sa limite d'utilisation, cet outil d'arrière-plan pour macOS sélectionne automatiquement **« Stop and wait for limit to reset »** — ainsi votre travail reprend tout seul dès que la limite est réinitialisée, même en votre absence.

[English](README.md) · [한국어](README.ko.md) · [中文](README.zh.md) · [日本語](README.ja.md) · [Español](README.es.md) · **Français** · [Deutsch](README.de.md) · [Português](README.pt.md) · [Русский](README.ru.md)

![Platform](https://img.shields.io/badge/platform-macOS-black) ![Python](https://img.shields.io/badge/python-3-blue) ![License](https://img.shields.io/badge/license-MIT-green)

```
What do you want to do?
❯ 1. Stop and wait for limit to reset
  2. Upgrade your plan
```

Fini de rester assis devant votre terminal à appuyer sur **« 1 »** dans ce menu. Éloignez-vous — le travail reprend à la réinitialisation, puis vous êtes averti sur **Discord / Telegram / Slack** pour savoir qu'il a continué.

**Le flux complet** : donnez l'URL git à Claude Code → `install.sh` → la commande `/continue` est installée → vous exécutez `/continue` et vous partez → à la réinitialisation des jetons, votre travail reprend automatiquement → vous recevez une notification par messagerie.

## Idée centrale : la surveillance ne coûte aucun jeton

Le surveillant est du **Python local qui se contente d'analyser votre écran et vos fichiers** — il n'appelle jamais Claude, donc il ne dépense aucun jeton (= aucun coût). Les jetons ne sont dépensés qu'au moment où le travail **reprend réellement**, et une seule fois par session à la réinitialisation. « N'est-ce pas coûteux de tourner chaque minute ? » → le sondage est gratuit ; seule la reprise coûte.

## Deux filets de sécurité

| Name | Target | Action | Interval |
|---|---|---|---|
| **tmux-resume** (core) | sessions de terminal actives à l'intérieur de tmux | gère **les deux formes de limite** (menu / en ligne), lit l'heure exacte de réinitialisation depuis l'API d'usage, et **tape `continue` à la réinitialisation** pour vraiment reprendre — vérifié de bout en bout | 60s |
| **resume-safety** (backup) | sessions en pause que vous avez laissées | analyse les journaux de conversation (jsonl) → `claude --resume` en arrière-plan à la réinitialisation | 300s |

Ils ne se chevauchent pas : `resume-safety` **cède la place** sur les projets qui ont une session active (pour éviter de se disputer le même quota de compte), et ce terminal est pris en charge par `tmux-resume`.

## Mode : jusqu'où la reprise est automatique

Vous choisissez via `resume_mode` dans `~/.config/claude-terminal-auto/notify.json` :

| Mode | Behavior | Tokens |
|---|---|---|
| **`token_only`** (par défaut, recommandé) | ne gère que le menu de limite d'utilisation. S'arrête et vous demande quand une tâche est terminée. | économe |
| **`keep_going`** | ce qui précède, plus **relance automatiquement les sessions inactives pour qu'elles continuent** une fois terminées → ne s'arrête jamais durant la nuit. | continue à dépenser |

```jsonc
{ "resume_mode": "token_only" }   // or "keep_going"
```

Garde-fous de `keep_going` : ne touche jamais à une session qui est en train de générer ou qui a un brouillon dans la zone de saisie ; temps de recharge de 15 minutes par volet. (Il continue à travailler de façon autonome et dépense des jetons, alors ne l'activez que si c'est ce que vous voulez.)

## Prérequis

- **macOS** (launchd)
- **tmux** — les sessions doivent tourner à l'intérieur de tmux pour l'injection de touches (macOS bloque l'injection de touches en dehors de tmux)
- **Python 3** — bibliothèque standard uniquement, rien à installer

## Installation

```bash
git clone https://github.com/dawith-ai/Dawith-Claude-terminal-auto-Mac.git
cd Dawith-Claude-terminal-auto-Mac
./install.sh
```

`install.sh` réécrit les chemins des plist vers ce dossier, les installe dans `~/Library/LaunchAgents`, et les enregistre auprès de launchd (survit au redémarrage). Si vous utilisez Claude Code, la **commande slash `/continue`** (et ses traductions) est aussi installée dans `~/.claude/commands/`. Vérifiez l'état :

```bash
launchctl list | grep claude-terminal-auto
```

## Désinstallation

```bash
./uninstall.sh
```

## La commande slash `/continue` (Claude Code)

Indépendamment du surveillant d'arrière-plan, taper **`/continue`** dans Claude Code déclenche le filet de sécurité une fois et reprend le travail interrompu.

- **Auto en arrière-plan** (launchd) = automatique à la réinitialisation, aucune saisie nécessaire
- **`/continue`** (slash) = un déclenchement manuel pour quand vous voulez démarrer immédiatement au lieu d'attendre

Des noms de commande localisés sont installés pour chaque langue, afin que vous puissiez l'utiliser dans la vôtre :

| Language | Command | Language | Command |
|---|---|---|---|
| English | `/continue` | Español | `/continuar` |
| 한국어 | `/지속` | Français | `/continuer` |
| 中文 | `/继续` | Deutsch | `/weiter` |
| 日本語 | `/続行` | Português | `/prosseguir` |
| Русский | `/продолжить` | | |

## Notifications par messagerie (Discord / Telegram / Slack / n'importe quel webhook)

Recevez un signal quand le travail reprend automatiquement. `install.sh` crée un modèle dans `~/.config/claude-terminal-auto/notify.json` ; **ne renseignez que les canaux que vous voulez** (laissez le reste vide pour les garder désactivés).

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

- **Ajouter d'autres messageries, de deux façons** :
  1. **Sans code** — ajoutez `{url, field, name}` à `generic_webhooks` (fonctionne avec Mattermost, Google Chat, les services compatibles Slack, et la plupart des services qui acceptent un POST JSON)
  2. **Fonction dédiée** — ajoutez une fonction `_send_*` dans `scripts/notify.py` et une ligne à `_SENDERS` (pour les formats spéciaux)
- Configurable aussi via des variables d'environnement : `CLAUDE_AUTO_DISCORD_WEBHOOK` / `CLAUDE_AUTO_TELEGRAM_TOKEN` / `CLAUDE_AUTO_TELEGRAM_CHAT_ID` / `CLAUDE_AUTO_SLACK_WEBHOOK`
- Test : `python3 scripts/notify.py "test"` → envoie vers les canaux configurés
- ⚠️ `notify.json` contient des jetons/webhooks, il est donc ignoré par git (le dépôt ne fournit qu'un `notify.example.json` vide).

## Comment ça marche

- **tmux-resume** (`scripts/tmux_resume_watcher.py`) : lit chaque volet avec `tmux capture-pane` et exécute un flux en **2 étapes** couvrant **les deux formes de limite** — le menu interactif (`What do you want to do?`) et le message en ligne (`You've hit your session limit · resets 3pm`). Pourquoi deux étapes : sélectionner « Stop and wait for limit to reset » ne reprend **pas** automatiquement de soi-même — Claude Code reste inactif à la réinitialisation jusqu'à ce que vous tapiez `continue` (un problème ouvert connu, [#18980](https://github.com/anthropics/claude-code/issues/18980) / [#35744](https://github.com/anthropics/claude-code/issues/35744)). Donc :
  1. **À la limite** — pour le menu, il appuie sur **`1` → `Enter`** (jamais `Esc`, qui annule le menu) ; il lit l'**heure exacte de réinitialisation depuis l'API d'usage** (`GET /api/oauth/usage` → `five_hour.resets_at`), avec un repli sur l'analyse de l'écran.
  2. **À l'heure de réinitialisation** — il tape **`continue`** (configurable via `continue_prompt`) pour vraiment reprendre le travail interrompu.

  Quand une session confirme qu'elle a repris, le cycle complet (limite détectée → `continue` envoyé → travail repris) est enregistré dans `/tmp/openclaw_tmux_resume_PROOF.log` et envoyé vers votre messagerie. **Garde-fous** : n'agit que lorsqu'une forme de limite est présente et que le volet est inactif (pas la barre de saisie normale `bypass permissions` / l'état de génération `esc to interrupt`).
- **resume-safety** (`scripts/resume_blocked_sessions.py`) : analyse les journaux de conversation sous `~/.claude/projects`, trouve les sessions bloquées par la limite, et les reprend avec un nouveau processus `claude --resume` à la réinitialisation. Il dispose de **garde-fous d'économie** (une reprise par fenêtre de session de 5 heures, un seuil de cession fondé sur l'usage de la session). Pour les exécutions sans interface, il lit le jeton OAuth de Claude depuis le trousseau macOS au moment de l'exécution — jamais stocké dans le code source.

## Notes

- Cet outil suppose une **exécution autonome** (« procéder sans demander ») lorsqu'il confirme le menu. Gardez-le à l'esprit lors de travaux sensibles.
- Si vous exécutez déjà les mêmes scripts sous une autre étiquette (par ex. `com.openclaw.*`), **ne les installez pas en double** (pour éviter un double déclenchement).

## Licence

MIT
