# Dawith Claude terminal auto (Mac)

> **No vuelvas a vigilar el límite de uso de Claude Code.** Cuando Claude Code alcanza su límite de uso, esta herramienta en segundo plano para macOS selecciona automáticamente **"Stop and wait for limit to reset"** — así tu trabajo se reanuda por sí solo en el momento en que el límite se restablece, incluso mientras estás ausente.

[English](README.md) · [한국어](README.ko.md) · [中文](README.zh.md) · [日本語](README.ja.md) · **Español** · [Français](README.fr.md) · [Deutsch](README.de.md) · [Português](README.pt.md) · [Русский](README.ru.md)

![Platform](https://img.shields.io/badge/platform-macOS-black) ![Python](https://img.shields.io/badge/python-3-blue) ![License](https://img.shields.io/badge/license-MIT-green)

```
What do you want to do?
❯ 1. Stop and wait for limit to reset
  2. Upgrade your plan
```

Se acabó tener que quedarte junto a tu terminal para pulsar **"1"** en este menú. Aléjate — se reanuda al restablecerse el límite y luego te avisa por **Discord / Telegram / Slack** para que sepas que continuó.

**El flujo completo**: entrega la URL de git a Claude Code → `install.sh` → se instala el comando `/continue` → ejecutas `/continue` y te marchas → al restablecerse los tokens tu trabajo se reanuda automáticamente → recibes una notificación en el mensajero.

## Idea central: vigilar cuesta cero tokens

El vigilante es **Python local que solo escanea tu pantalla y tus archivos** — nunca llama a Claude, así que gasta cero tokens (= cero coste). Los tokens se gastan únicamente en el momento en que el trabajo **realmente se reanuda**, y solo una vez por sesión al restablecerse. "¿No es caro ejecutarlo cada minuto?" → el sondeo es gratis; solo la reanudación cuesta.

## Dos redes de seguridad

| Name | Target | Action | Interval |
|---|---|---|---|
| **tmux-resume** (core) | sesiones de terminal activas dentro de tmux | gestiona **ambas formas de límite** (menú / en línea), lee la hora exacta de restablecimiento desde la API de uso, y **escribe `continue` al restablecerse** para reanudar de verdad — verificado de extremo a extremo | 60s |
| **resume-safety** (backup) | sesiones aparcadas que dejaste al marcharte | escanea los registros de conversación (jsonl) → `claude --resume` en segundo plano al restablecerse | 300s |

No se solapan: `resume-safety` **cede el paso** en los proyectos que tienen una sesión activa (para evitar competir por la misma cuota de la cuenta), y esa terminal la gestiona `tmux-resume`.

## Modo: hasta dónde continúa automáticamente

Lo eliges mediante `resume_mode` en `~/.config/claude-terminal-auto/notify.json`:

| Mode | Behavior | Tokens |
|---|---|---|
| **`token_only`** (predeterminado, recomendado) | gestiona solo el menú de límite de uso. Se detiene y pregunta cuando termina una tarea. | frugal |
| **`keep_going`** | lo anterior, más **da un empujón automático a las sesiones inactivas para que sigan** una vez terminan → nunca se detiene durante la noche. | keeps spending |

```jsonc
{ "resume_mode": "token_only" }   // or "keep_going"
```

Salvaguardas de `keep_going`: nunca toca una sesión que esté generando o que tenga un borrador en el cuadro de entrada; enfriamiento de 15 minutos por panel. (Sigue trabajando de forma autónoma y gasta tokens, así que actívalo solo cuando quieras eso.)

## Requisitos

- **macOS** (launchd)
- **tmux** — las sesiones deben ejecutarse dentro de tmux para la inyección de teclas (macOS bloquea la inyección de teclas fuera de tmux)
- **Python 3** — solo biblioteca estándar, nada que instalar

## Instalación

```bash
git clone https://github.com/dawith-ai/Dawith-Claude-terminal-auto-Mac.git
cd Dawith-Claude-terminal-auto-Mac
./install.sh
```

`install.sh` reescribe las rutas de los plist hacia esta carpeta, los instala en `~/Library/LaunchAgents` y los registra con launchd (sobrevive al reinicio). Si usas Claude Code, el **comando de barra `/continue`** (y sus traducciones) se instala también en `~/.claude/commands/`. Comprueba el estado:

```bash
launchctl list | grep claude-terminal-auto
```

## Desinstalación

```bash
./uninstall.sh
```

## El comando de barra `/continue` (Claude Code)

Aparte del vigilante en segundo plano, escribir **`/continue`** en Claude Code activa la red de seguridad una vez y reanuda el trabajo interrumpido.

- **Auto en segundo plano** (launchd) = automático al restablecerse, sin necesidad de escribir nada
- **`/continue`** (barra) = un disparador manual para cuando quieres empezar de inmediato en lugar de esperar

Se instalan nombres de comando localizados para cada idioma, así que puedes usarlo en el tuyo:

| Language | Command | Language | Command |
|---|---|---|---|
| English | `/continue` | Español | `/continuar` |
| 한국어 | `/지속` | Français | `/continuer` |
| 中文 | `/继续` | Deutsch | `/weiter` |
| 日本語 | `/続行` | Português | `/prosseguir` |
| Русский | `/продолжить` | | |

## Notificaciones por mensajero (Discord / Telegram / Slack / cualquier webhook)

Recibe un aviso cuando el trabajo se reanuda automáticamente. `install.sh` crea una plantilla en `~/.config/claude-terminal-auto/notify.json`; **rellena solo los canales que quieras** (deja el resto vacíos para mantenerlos desactivados).

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

- **Añade más mensajeros, de dos formas**:
  1. **Sin código** — añade `{url, field, name}` a `generic_webhooks` (funciona con Mattermost, Google Chat, compatibles con Slack y la mayoría de servicios que aceptan un POST JSON)
  2. **Función dedicada** — añade una función `_send_*` en `scripts/notify.py` y una línea a `_SENDERS` (para formatos especiales)
- También configurable mediante variables de entorno: `CLAUDE_AUTO_DISCORD_WEBHOOK` / `CLAUDE_AUTO_TELEGRAM_TOKEN` / `CLAUDE_AUTO_TELEGRAM_CHAT_ID` / `CLAUDE_AUTO_SLACK_WEBHOOK`
- Prueba: `python3 scripts/notify.py "test"` → envía a los canales configurados
- ⚠️ `notify.json` contiene tokens/webhooks, por lo que está ignorado por git (el repositorio incluye solo un `notify.example.json` vacío).

## Cómo funciona

- **tmux-resume** (`scripts/tmux_resume_watcher.py`): lee cada panel con `tmux capture-pane` y ejecuta un flujo de **2 pasos** a través de **ambas formas de límite** — el menú interactivo (`What do you want to do?`) y el mensaje en línea (`You've hit your session limit · resets 3pm`). Por qué dos pasos: seleccionar "Stop and wait for limit to reset" **no** reanuda por sí solo — Claude Code se queda inactivo al restablecerse hasta que escribes `continue` (un problema abierto conocido, [#18980](https://github.com/anthropics/claude-code/issues/18980) / [#35744](https://github.com/anthropics/claude-code/issues/35744)). Así que:
  1. **En el límite** — para el menú pulsa **`1` → `Enter`** (nunca `Esc`, que cancela el menú); lee la **hora exacta de restablecimiento desde la API de uso** (`GET /api/oauth/usage` → `five_hour.resets_at`), recurriendo al análisis de la pantalla como alternativa.
  2. **A la hora de restablecimiento** — escribe **`continue`** (configurable mediante `continue_prompt`) para reanudar de verdad el trabajo interrumpido.

  Cuando una sesión confirma que se reanudó, el ciclo completo (límite detectado → `continue` enviado → trabajo reanudado) se registra en `/tmp/openclaw_tmux_resume_PROOF.log` y se envía a tu mensajero. **Protecciones**: actúa solo cuando hay una forma de límite presente y el panel está inactivo (no la barra de entrada normal `bypass permissions` / el estado de generación `esc to interrupt`).
- **resume-safety** (`scripts/resume_blocked_sessions.py`): escanea los registros de conversación bajo `~/.claude/projects`, encuentra las sesiones bloqueadas por el límite y las reanuda con un nuevo proceso `claude --resume` al restablecerse. Tiene **protecciones de frugalidad** (una reanudación por ventana de sesión de 5 horas, un umbral de cesión según el uso de la sesión). Para ejecuciones sin interfaz, lee el token OAuth de Claude desde el keychain de macOS en tiempo de ejecución — nunca se almacena en el código fuente.

## Notas

- Esta herramienta asume **ejecución autónoma** ("proceder sin preguntar") cuando confirma el menú. Tenlo en cuenta durante trabajos delicados.
- Si ya ejecutas los mismos scripts bajo otra etiqueta (p. ej. `com.openclaw.*`), **no los instales por duplicado** (evita disparos dobles).

## Licencia

MIT
