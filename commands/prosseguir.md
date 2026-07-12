---
name: prosseguir
description: Continuar autonomamente o trabalho anterior + disparar a rede de segurança de retomada uma vez (manter simples a mensagem voltada ao usuário)
---

Continue o trabalho anterior de forma autônoma.

## Passo 1 — Disparar a rede de segurança uma vez (silenciosamente)

Execute o seguinte, mas **nunca mostre ao usuário a saída técnica** (números de varredura/disparo/pulo — descarte a saída):

```bash
__PYTHON__ __REPO_DIR__/scripts/resume_blocked_sessions.py > /dev/null 2>&1
```

## Passo 2 — Continuar o trabalho anterior

- Continue imediatamente qualquer trabalho inacabado sem fazer perguntas de esclarecimento.
- Relate em uma linha quando uma etapa for concluída.

## Mensagem voltada ao usuário — mantenha simples (importante)

**Não** exponha termos técnicos ou números como "rede de segurança", "varredura N", "disparo", "pular bloqueadas", "reinício HH:MM".
Apenas uma linha neste tom:

> **"Claro, continuando."**

(Adicione no máximo mais uma linha se ajudar: "Outras sessões também vão continuar automaticamente quando o limite delas for reiniciado.")

Depois continue o trabalho imediatamente.
