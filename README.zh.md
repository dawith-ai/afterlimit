# Dawith Claude terminal auto (Mac)

> **再也不用守着 Claude Code 的用量上限了。** 当 Claude Code 触及用量上限时，这个 macOS 后台工具会自动选择 **“Stop and wait for limit to reset”**（停止并等待上限重置）——于是在上限重置的那一刻，你的工作会自行恢复，哪怕你人不在电脑前。

[English](README.md) · [한국어](README.ko.md) · **中文** · [日本語](README.ja.md) · [Español](README.es.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · [Português](README.pt.md) · [Русский](README.ru.md)

![Platform](https://img.shields.io/badge/platform-macOS-black) ![Python](https://img.shields.io/badge/python-3-blue) ![License](https://img.shields.io/badge/license-MIT-green)

```
What do you want to do?
❯ 1. Stop and wait for limit to reset
  2. Upgrade your plan
```

再也不用坐在终端前为这个菜单去按 **“1”** 了。走开就好——它会在上限重置时恢复，然后通过 **Discord / Telegram / Slack** 给你发通知，让你知道它已经继续了。

**完整流程**：把 git URL 交给 Claude Code → `install.sh` → 安装好 `/continue` 命令 → 你运行 `/continue` 然后离开 → 在 token 重置时你的工作自动恢复 → 你收到一条即时通讯通知。

## 核心理念：监视本身不花任何 token

这个监视器是 **只扫描你的屏幕和文件的本地 Python 程序**——它从不调用 Claude，因此花费零 token（= 零成本）。token 只在工作 **真正恢复** 的那一刻花费，而且每个会话在重置时只花一次。“每分钟都跑一次难道不贵吗？” → 轮询是免费的，只有恢复才花钱。

## 两道安全网

| Name | Target | Action | Interval |
|---|---|---|---|
| **tmux-resume**（核心） | tmux 内的实时终端会话 | 检测到上限菜单 → 自动选择选项 1 “Stop and wait for limit to reset”（`1` → `Enter`） | 60s |
| **resume-safety**（备份） | 你走开后搁置的会话 | 扫描对话日志（jsonl）→ 在重置时于后台执行 `claude --resume` | 300s |

它们不会重叠：对于已有实时会话的项目，`resume-safety` 会 **让位**（以免争抢同一账户的额度），那个终端会由 `tmux-resume` 负责。

## 模式：自动继续到什么程度

你可以通过 `~/.config/claude-terminal-auto/notify.json` 里的 `resume_mode` 来选择：

| Mode | Behavior | Tokens |
|---|---|---|
| **`token_only`**（默认，推荐） | 只处理用量上限菜单。任务完成时会停下来并询问。 | 省钱 |
| **`keep_going`** | 在上述基础上，还会 **自动推动空闲会话继续干下去**，等它们完成后再推 → 整夜都不停。 | 持续花钱 |

```jsonc
{ "resume_mode": "token_only" }   // or "keep_going"
```

`keep_going` 的保护机制：绝不碰正在生成、或输入框里已有草稿的会话；每个窗格有 15 分钟冷却时间。（它会自主地一直工作并花费 token，所以只在你确实想要这样时才启用。）

## 环境要求

- **macOS**（launchd）
- **tmux** —— 会话必须在 tmux 内运行才能进行按键注入（macOS 会阻止向 tmux 之外注入按键）
- **Python 3** —— 仅用标准库，无需安装任何东西

## 安装

```bash
git clone https://github.com/dawith-ai/Dawith-Claude-terminal-auto-Mac.git
cd Dawith-Claude-terminal-auto-Mac
./install.sh
```

`install.sh` 会把 plist 里的路径改写为这个文件夹，将它们安装到 `~/Library/LaunchAgents`，并注册到 launchd（重启后依然有效）。如果你使用 Claude Code，**`/continue` 斜杠命令**（及其各语言翻译版）也会被安装到 `~/.claude/commands/`。查看状态：

```bash
launchctl list | grep claude-terminal-auto
```

## 卸载

```bash
./uninstall.sh
```

## `/continue` 斜杠命令（Claude Code）

与后台监视器相互独立，在 Claude Code 中输入 **`/continue`** 会触发一次安全网，并恢复被中断的工作。

- **后台自动**（launchd）= 在重置时自动进行，无需输入
- **`/continue`**（斜杠命令）= 当你想立即开始、而不愿等待时的手动触发

每种语言都安装了本地化的命令名，因此你可以用你自己的语言来使用它：

| Language | Command | Language | Command |
|---|---|---|---|
| English | `/continue` | Español | `/continuar` |
| 한국어 | `/지속` | Français | `/continuer` |
| 中文 | `/继续` | Deutsch | `/weiter` |
| 日本語 | `/続行` | Português | `/prosseguir` |
| Русский | `/продолжить` | | |

## 即时通讯通知（Discord / Telegram / Slack / 任意 webhook）

在工作自动恢复时收到一条通知。`install.sh` 会在 `~/.config/claude-terminal-auto/notify.json` 创建一个模板；**只填写你想用的渠道**（其余留空即可保持关闭）。

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

- **添加更多即时通讯工具，有两种方式**：
  1. **无需写代码** —— 往 `generic_webhooks` 里加一个 `{url, field, name}`（适用于 Mattermost、Google Chat、Slack 兼容服务，以及大多数接受 JSON POST 的服务）
  2. **专用函数** —— 在 `scripts/notify.py` 里加一个 `_send_*` 函数，并在 `_SENDERS` 里加一行（用于特殊格式）
- 也可以通过环境变量配置：`CLAUDE_AUTO_DISCORD_WEBHOOK` / `CLAUDE_AUTO_TELEGRAM_TOKEN` / `CLAUDE_AUTO_TELEGRAM_CHAT_ID` / `CLAUDE_AUTO_SLACK_WEBHOOK`
- 测试：`python3 scripts/notify.py "test"` → 发送到已配置的渠道
- ⚠️ `notify.json` 里保存着 token/webhook，因此它被 git 忽略（仓库只附带一个空的 `notify.example.json`）。

## 工作原理

- **tmux-resume**（`scripts/tmux_resume_watcher.py`）：用 `tmux capture-pane` 读取每个窗格；当上限菜单（`What do you want to do? / 1. Stop and wait for limit to reset / 2. Upgrade your plan`）在底部处于 **激活** 状态时，它用 `tmux send-keys` 按下 **`1` → `Enter`**，确认选项 1 → Claude 在重置时恢复你的工作。（⚠️ 它绝不会发送 `Esc`，因为那会取消菜单。）**防误报保护**：仅当三条菜单短语全部出现、且不存在正常的输入栏（`bypass permissions`）/ 生成状态（`esc to interrupt`）时才触发；每个窗格有 5 分钟冷却时间。
- **resume-safety**（`scripts/resume_blocked_sessions.py`）：扫描 `~/.claude/projects` 下的对话日志，找出被上限卡住的会话，并在重置时用一个全新的 `claude --resume` 进程恢复它们。它带有 **省钱保护**（每个 5 小时的会话窗口只恢复一次，以及一个会话用量的让位阈值）。对于无头运行，它在运行时从 macOS keychain 读取 Claude OAuth token —— 绝不存放在源代码中。

## 说明

- 本工具在确认菜单时会假定 **自主执行**（“不询问直接进行”）。在敏感工作中请留意这一点。
- 如果你已经用另一个标签（例如 `com.openclaw.*`）运行着相同的脚本，请 **不要重复安装**（以避免重复触发）。

## 许可协议

MIT
