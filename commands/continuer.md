---
name: continuer
description: Poursuivre de façon autonome le travail précédent + déclencher une fois le filet de sécurité de reprise (garder le message destiné à l'utilisateur simple)
---

Poursuivez le travail précédent de façon autonome.

## Étape 1 — Déclencher le filet de sécurité une fois (silencieusement)

Exécutez ce qui suit, mais **ne montrez jamais à l'utilisateur la sortie technique** (chiffres de scan/déclenchement/saut — jetez la sortie) :

```bash
__PYTHON__ __REPO_DIR__/scripts/resume_blocked_sessions.py > /dev/null 2>&1
```

## Étape 2 — Poursuivre le travail précédent

- Poursuivez immédiatement tout travail inachevé sans poser de questions de clarification.
- Signalez en une ligne quand une étape est terminée.

## Message destiné à l'utilisateur — restez simple (important)

Ne **révélez pas** de termes techniques ou de chiffres comme « filet de sécurité », « scan N », « déclenchement », « sauter les bloqués », « réinitialisation HH:MM ».
Juste une ligne sur ce ton :

> **« D'accord, je continue. »**

(Ajoutez au plus une ligne de plus si c'est utile : « Les autres sessions reprendront aussi automatiquement quand leur limite sera réinitialisée. »)

Puis poursuivez le travail tout de suite.
