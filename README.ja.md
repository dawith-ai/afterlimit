# Dawith Claude terminal auto (Mac)

> **Claude Code の使用量上限をもう見守らなくて大丈夫。** Claude Code が使用量上限に達すると、この macOS バックグラウンドツールが自動で **「Stop and wait for limit to reset」** を選択します。だから席を外していても、上限がリセットされた瞬間に作業がひとりでに再開します。

[English](README.md) · [한국어](README.ko.md) · [中文](README.zh.md) · **日本語** · [Español](README.es.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · [Português](README.pt.md) · [Русский](README.ru.md)

![Platform](https://img.shields.io/badge/platform-macOS-black) ![Python](https://img.shields.io/badge/python-3-blue) ![License](https://img.shields.io/badge/license-MIT-green)

```
What do you want to do?
❯ 1. Stop and wait for limit to reset
  2. Upgrade your plan
```

このメニューで **「1」** を押すためにターミナルの前に張り付く必要はもうありません。席を離れても、リセット時に自動再開し、**Discord / Telegram / Slack** に通知が届くので、続いたことがわかります。

**全体の流れ**: git URL を Claude Code に渡す → `install.sh` → `/continue` コマンドがインストールされる → `/continue` を実行して離席 → トークンのリセット時に作業が自動再開 → メッセンジャー通知が届く。

## 中心となる考え方: 見張りはトークンを一切消費しない

ウォッチャーは **画面とファイルをスキャンするだけのローカル Python** で、Claude を呼び出すことは一切ないため、トークンをゼロ消費します(= コストゼロ)。トークンが消費されるのは作業が **実際に再開する** 瞬間だけで、しかもリセットごとに 1 セッションにつき 1 回だけです。「毎分動かしたら高くつくのでは?」→ ポーリングは無料で、コストがかかるのは再開だけです。

## 2 つの安全網

| Name | Target | Action | Interval |
|---|---|---|---|
| **tmux-resume** (core) | tmux 内で動いているライブのターミナルセッション | 上限メニューを検出 → オプション 1「Stop and wait for limit to reset」を自動選択(`1` → `Enter`) | 60s |
| **resume-safety** (backup) | 離席して放置したセッション | 会話ログ(jsonl)をスキャン → リセット時にバックグラウンドで `claude --resume` | 300s |

両者は重複しません。`resume-safety` はライブセッションのあるプロジェクトでは **譲り**(同じアカウントの割り当てを取り合わないため)、そのターミナルは `tmux-resume` が担当します。

## モード: どこまで自動継続するか

`~/.config/claude-terminal-auto/notify.json` の `resume_mode` で選びます:

| Mode | Behavior | Tokens |
|---|---|---|
| **`token_only`** (デフォルト・推奨) | 使用量上限のメニューだけを処理します。タスクが完了すると停止して確認します。 | 倹約的 |
| **`keep_going`** | 上記に加えて、**アイドル状態のセッションを完了後に自動で促して継続させます** → 夜通し止まりません。 | 消費し続ける |

```jsonc
{ "resume_mode": "token_only" }   // or "keep_going"
```

`keep_going` の安全策: 生成中のセッションや入力欄に下書きがあるセッションには一切手を触れません。ペインごとに 15 分のクールダウン。(自律的に作業を続けてトークンを消費するので、それを望むときだけ有効にしてください。)

## 必要要件

- **macOS** (launchd)
- **tmux** — キー入力の注入のためセッションは tmux 内で動かす必要があります(macOS は tmux 外へのキー注入をブロックします)
- **Python 3** — 標準ライブラリのみ、インストール不要

## インストール

```bash
git clone https://github.com/dawith-ai/Dawith-Claude-terminal-auto-Mac.git
cd Dawith-Claude-terminal-auto-Mac
./install.sh
```

`install.sh` は plist のパスをこのフォルダに書き換え、`~/Library/LaunchAgents` にインストールし、launchd に登録します(再起動後も生き残ります)。Claude Code を使っている場合は、**`/continue` スラッシュコマンド**(とその翻訳)も `~/.claude/commands/` にインストールされます。状態の確認:

```bash
launchctl list | grep claude-terminal-auto
```

## アンインストール

```bash
./uninstall.sh
```

## `/continue` スラッシュコマンド (Claude Code)

バックグラウンドのウォッチャーとは別に、Claude Code で **`/continue`** と入力すると安全網が 1 回発火し、中断された作業を再開します。

- **バックグラウンド自動** (launchd) = リセット時に自動、入力不要
- **`/continue`** (スラッシュ) = 待たずにすぐ始めたいときの手動トリガー

各言語向けにローカライズされたコマンド名がインストールされるので、あなたの言語で使えます:

| Language | Command | Language | Command |
|---|---|---|---|
| English | `/continue` | Español | `/continuar` |
| 한국어 | `/지속` | Français | `/continuer` |
| 中文 | `/继续` | Deutsch | `/weiter` |
| 日本語 | `/続行` | Português | `/prosseguir` |
| Русский | `/продолжить` | | |

## メッセンジャー通知 (Discord / Telegram / Slack / 任意の webhook)

作業が自動再開したときに通知を受け取れます。`install.sh` が `~/.config/claude-terminal-auto/notify.json` にテンプレートを作成するので、**使いたいチャンネルだけを埋めてください**(残りは空のままにしてオフに保ちます)。

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

- **メッセンジャーを増やす方法は 2 通り**:
  1. **コード不要** — `generic_webhooks` に `{url, field, name}` を追加(Mattermost、Google Chat、Slack 互換、および JSON POST を受け付けるほとんどのサービスで動作します)
  2. **専用関数** — `scripts/notify.py` に `_send_*` 関数を追加し、`_SENDERS` に 1 行追加(特殊なフォーマット向け)
- 環境変数でも設定可能: `CLAUDE_AUTO_DISCORD_WEBHOOK` / `CLAUDE_AUTO_TELEGRAM_TOKEN` / `CLAUDE_AUTO_TELEGRAM_CHAT_ID` / `CLAUDE_AUTO_SLACK_WEBHOOK`
- テスト: `python3 scripts/notify.py "test"` → 設定済みのチャンネルに送信します
- ⚠️ `notify.json` はトークンや webhook を保持するため git-ignore されています(リポジトリには空の `notify.example.json` のみが同梱されます)。

## 仕組み

- **tmux-resume** (`scripts/tmux_resume_watcher.py`): `tmux capture-pane` で各ペインを読み取り、上限メニュー(`What do you want to do? / 1. Stop and wait for limit to reset / 2. Upgrade your plan`)が最下部で **アクティブ** なとき、`tmux send-keys` で **`1` → `Enter`** を押してオプション 1 を確定します → Claude がリセット時に作業を再開します。(⚠️ メニューをキャンセルしてしまう `Esc` は決して送りません。)**誤検出ガード**: 3 つのメニューフレーズがすべて存在し、かつ通常の入力バー(`bypass permissions`)/ 生成中の状態(`esc to interrupt`)がないときだけ発火します。ペインごとに 5 分のクールダウン。
- **resume-safety** (`scripts/resume_blocked_sessions.py`): `~/.claude/projects` 以下の会話ログをスキャンし、上限でブロックされたセッションを見つけ、リセット時に新しい `claude --resume` プロセスで再開します。**倹約ガード**(5 時間のセッションウィンドウにつき 1 回の再開、セッション使用量の譲歩しきい値)を備えています。ヘッドレス実行時は、実行時に macOS キーチェーンから Claude の OAuth トークンを読み取ります。ソースには決して保存しません。

## 注意

- このツールはメニューを確定するとき **自律実行**(「確認せず進める」)を前提とします。慎重な作業のときはその点に留意してください。
- 同じスクリプトをすでに別のラベル(例: `com.openclaw.*`)で動かしている場合は、**二重にインストールしないでください**(二重発火を避けるため)。

## License

MIT
