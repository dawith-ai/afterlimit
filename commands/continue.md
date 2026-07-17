---
name: continue
description: Autonomously continue previous work + fire the resume safety net once (keep the user-facing message simple)
---

Continue the previous work autonomously.

## Step 1 — Fire the safety net once (silently)

Run the following, but **never show the user the technical output** (scan/fire/skip numbers — discard the output):

```bash
afterlimit run > /dev/null 2>&1
```

## Step 2 — Continue the previous work

- Immediately continue any unfinished work without asking clarifying questions.
- Report in one line when a step is done.

## User-facing message — keep it simple (important)

Do **not** expose technical terms or numbers like "safety net", "scan N", "fire", "skip blocked", "reset HH:MM".
Just one line in this tone:

> **"Sure, continuing."**

(Add at most one more line if helpful: "Other sessions will also auto-continue when their limit resets.")

Then continue the work right away.
