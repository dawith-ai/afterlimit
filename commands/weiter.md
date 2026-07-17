---
name: weiter
description: Vorherige Arbeit autonom fortsetzen + das Resume-Sicherheitsnetz einmalig auslösen (die für den Benutzer sichtbare Nachricht einfach halten)
---

Setze die vorherige Arbeit autonom fort.

## Schritt 1 — Das Sicherheitsnetz einmalig auslösen (im Stillen)

Führe Folgendes aus, aber **zeige dem Benutzer niemals die technische Ausgabe** (Scan-/Auslöse-/Skip-Zahlen — verwirf die Ausgabe):

```bash
afterlimit run > /dev/null 2>&1
```

## Schritt 2 — Die vorherige Arbeit fortsetzen

- Setze jede unfertige Arbeit sofort fort, ohne klärende Rückfragen zu stellen.
- Melde in einer Zeile, wenn ein Schritt erledigt ist.

## Für den Benutzer sichtbare Nachricht — halte sie einfach (wichtig)

Lege **keine** technischen Begriffe oder Zahlen offen wie "Sicherheitsnetz", "Scan N", "auslösen", "blockierte überspringen", "Reset HH:MM".
Nur eine Zeile in diesem Ton:

> **"Klar, ich mache weiter."**

(Füge höchstens eine weitere Zeile hinzu, falls hilfreich: "Andere Sitzungen setzen sich ebenfalls automatisch fort, sobald ihr Limit zurückgesetzt wird.")

Setze die Arbeit dann sofort fort.
