---
name: continuar
description: Continuar de forma autónoma el trabajo anterior + activar una vez la red de seguridad de reanudación (mantén simple el mensaje visible para el usuario)
---

Continúa el trabajo anterior de forma autónoma.

## Paso 1 — Activa la red de seguridad una vez (en silencio)

Ejecuta lo siguiente, pero **nunca muestres al usuario la salida técnica** (números de escaneo/activación/omisión — descarta la salida):

```bash
__PYTHON__ __REPO_DIR__/scripts/resume_blocked_sessions.py > /dev/null 2>&1
```

## Paso 2 — Continúa el trabajo anterior

- Continúa de inmediato cualquier trabajo sin terminar sin hacer preguntas aclaratorias.
- Informa en una sola línea cuando se complete un paso.

## Mensaje visible para el usuario — mantenlo simple (importante)

**No** expongas términos técnicos ni números como "red de seguridad", "escanear N", "activar", "omitir bloqueados", "restablecer HH:MM".
Solo una línea con este tono:

> **"Claro, continúo."**

(Añade como mucho una línea más si resulta útil: "Otras sesiones también continuarán automáticamente cuando su límite se restablezca.")

Luego continúa el trabajo de inmediato.
