# Dawith Claude terminal auto (Mac)

> **Nunca mais fique de babá do limite de uso do Claude Code.** Quando o Claude Code atinge o limite de uso, esta ferramenta em segundo plano para macOS seleciona automaticamente **"Stop and wait for limit to reset"** — assim seu trabalho é retomado sozinho no exato momento em que o limite é reiniciado, mesmo que você esteja longe.

[English](README.md) · [한국어](README.ko.md) · [中文](README.zh.md) · [日本語](README.ja.md) · [Español](README.es.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · **Português** · [Русский](README.ru.md)

![Platform](https://img.shields.io/badge/platform-macOS-black) ![Python](https://img.shields.io/badge/python-3-blue) ![License](https://img.shields.io/badge/license-MIT-green)

```
What do you want to do?
❯ 1. Stop and wait for limit to reset
  2. Upgrade your plan
```

Chega de ficar sentado no terminal para apertar **"1"** neste menu. Vá embora — ele retoma no reinício e então te avisa no **Discord / Telegram / Slack** para você saber que continuou.

**O fluxo completo**: entregue a URL do git ao Claude Code → `install.sh` → o comando `/continue` é instalado → você executa `/continue` e vai embora → no reinício do token seu trabalho é retomado automaticamente → você recebe uma notificação no mensageiro.

## Ideia central: observar custa zero tokens

O observador é **Python local que apenas varre sua tela e seus arquivos** — ele nunca chama o Claude, então gasta zero tokens (= custo zero). Tokens são gastos apenas no momento em que o trabalho **de fato é retomado**, e apenas uma vez por sessão no reinício. "Não é caro rodar a cada minuto?" → a sondagem é grátis; só a retomada custa.

## Duas redes de segurança

| Name | Target | Action | Interval |
|---|---|---|---|
| **tmux-resume** (core) | live terminal sessions inside tmux | lida com **ambos os formatos de limite** (menu / inline), lê o horário exato de reinício da API de uso e **digita `continue` no reinício** para de fato retomar — verificado de ponta a ponta | 60s |
| **resume-safety** (backup) | parked sessions you walked away from | scans conversation logs (jsonl) → `claude --resume` in the background at reset | 300s |

Elas não se sobrepõem: o `resume-safety` **cede** nos projetos que têm uma sessão ativa (para evitar competir pela mesma cota da conta), e esse terminal é tratado pelo `tmux-resume`.

## Modo: até onde ele continua automaticamente

Você escolhe pela `resume_mode` em `~/.config/claude-terminal-auto/notify.json`:

| Mode | Behavior | Tokens |
|---|---|---|
| **`token_only`** (default, recommended) | handles only the usage-limit menu. Stops and asks when a task finishes. | frugal |
| **`keep_going`** | the above, plus **auto-nudges idle sessions to keep going** after they finish → never stops overnight. | keeps spending |

```jsonc
{ "resume_mode": "token_only" }   // or "keep_going"
```

Proteções do `keep_going`: nunca mexe em uma sessão que está gerando ou que tem um rascunho na caixa de entrada; tempo de espera de 15 minutos por painel. (Ele continua trabalhando de forma autônoma e gasta tokens, então ative-o somente quando você quiser isso.)

## Requisitos

- **macOS** (launchd)
- **tmux** — as sessões precisam rodar dentro do tmux para a injeção de teclas (o macOS bloqueia a injeção de teclas fora do tmux)
- **Python 3** — apenas a biblioteca padrão, nada para instalar

## Instalação

```bash
git clone https://github.com/dawith-ai/Dawith-Claude-terminal-auto-Mac.git
cd Dawith-Claude-terminal-auto-Mac
./install.sh
```

O `install.sh` reescreve os caminhos do plist para esta pasta, instala-os em `~/Library/LaunchAgents` e os registra no launchd (sobrevive a reinicializações). Se você usa o Claude Code, o **comando de barra `/continue`** (e suas traduções) também é instalado em `~/.claude/commands/`. Verifique o status:

```bash
launchctl list | grep claude-terminal-auto
```

## Desinstalação

```bash
./uninstall.sh
```

## O comando de barra `/continue` (Claude Code)

Separado do observador em segundo plano, digitar **`/continue`** no Claude Code dispara a rede de segurança uma vez e retoma o trabalho interrompido.

- **Auto em segundo plano** (launchd) = automático no reinício, sem precisar digitar
- **`/continue`** (barra) = um gatilho manual para quando você quer começar imediatamente em vez de esperar

Nomes de comando localizados são instalados para todos os idiomas, então você pode usá-lo no seu:

| Language | Command | Language | Command |
|---|---|---|---|
| English | `/continue` | Español | `/continuar` |
| 한국어 | `/지속` | Français | `/continuer` |
| 中文 | `/继续` | Deutsch | `/weiter` |
| 日本語 | `/続行` | Português | `/prosseguir` |
| Русский | `/продолжить` | | |

## Notificações por mensageiro (Discord / Telegram / Slack / qualquer webhook)

Receba um aviso quando o trabalho for retomado automaticamente. O `install.sh` cria um modelo em `~/.config/claude-terminal-auto/notify.json`; **preencha apenas os canais que você quer** (deixe o restante vazio para mantê-los desligados).

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

- **Adicione mais mensageiros, de duas formas**:
  1. **Sem código** — adicione `{url, field, name}` a `generic_webhooks` (funciona com Mattermost, Google Chat, compatíveis com Slack e a maioria dos serviços que aceitam um POST JSON)
  2. **Função dedicada** — adicione uma função `_send_*` em `scripts/notify.py` e uma linha a `_SENDERS` (para formatos especiais)
- Também configurável por variáveis de ambiente: `CLAUDE_AUTO_DISCORD_WEBHOOK` / `CLAUDE_AUTO_TELEGRAM_TOKEN` / `CLAUDE_AUTO_TELEGRAM_CHAT_ID` / `CLAUDE_AUTO_SLACK_WEBHOOK`
- Teste: `python3 scripts/notify.py "test"` → envia aos canais configurados
- ⚠️ O `notify.json` guarda tokens/webhooks, então ele é ignorado pelo git (o repositório traz apenas um `notify.example.json` vazio).

## Como funciona

- **tmux-resume** (`scripts/tmux_resume_watcher.py`): lê cada painel com `tmux capture-pane` e executa um fluxo de **2 passos** em **ambos os formatos de limite** — o menu interativo (`What do you want to do?`) e a mensagem inline (`You've hit your session limit · resets 3pm`). Por que dois passos: selecionar "Stop and wait for limit to reset" **não** retoma sozinho — o Claude Code fica ocioso no reinício até você digitar `continue` (um problema conhecido em aberto, [#18980](https://github.com/anthropics/claude-code/issues/18980) / [#35744](https://github.com/anthropics/claude-code/issues/35744)). Então:
  1. **No limite** — para o menu ele aperta **`1` → `Enter`** (nunca `Esc`, que cancela o menu); ele lê o **horário exato de reinício da API de uso** (`GET /api/oauth/usage` → `five_hour.resets_at`), recorrendo à leitura da tela como alternativa.
  2. **No horário de reinício** — ele digita **`continue`** (configurável via `continue_prompt`) para de fato retomar o trabalho interrompido.

  Quando uma sessão confirma que retomou, o ciclo completo (limite detectado → `continue` enviado → trabalho retomado) é registrado em `/tmp/openclaw_tmux_resume_PROOF.log` e enviado ao seu mensageiro. **Proteções**: age apenas quando um formato de limite está presente e o painel está ocioso (não a barra de entrada normal `bypass permissions` / o estado de geração `esc to interrupt`).
- **resume-safety** (`scripts/resume_blocked_sessions.py`): varre os logs de conversa em `~/.claude/projects`, encontra sessões bloqueadas por limite e as retoma com um novo processo `claude --resume` no reinício. Ele tem **proteções de frugalidade** (uma retomada por janela de sessão de 5 horas, um limiar de cessão por uso da sessão). Em execuções headless, ele lê o token OAuth do Claude do keychain do macOS em tempo de execução — nunca armazenado no código-fonte.

## Notas

- Esta ferramenta pressupõe **execução autônoma** ("prosseguir sem perguntar") quando confirma o menu. Tenha isso em mente durante trabalhos sensíveis.
- Se você já roda os mesmos scripts sob um rótulo diferente (ex.: `com.openclaw.*`), **não instale em dobro** (evita disparo duplo).

## Licença

MIT
