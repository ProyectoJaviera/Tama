# Evals de memoria

Generado por `python -m evals.memoria` con datos ficticios. Día 1: 10 hechos en una
conversación natural. Se reinicia Tama. Día 2: una pregunta por hecho, cada una en una
sesión nueva. Casos negativos: datos nunca contados. Meta: 9 de 10 y ninguna invención.

| Capa | Hechos recordados | Negativos | Entidades | Duplicadas | Mal nombradas | Tokens de prompt (máx) |
|---|---:|---:|---:|---:|---:|---:|
| nube | 9/10 | 3/3 | 8 | 0 | 0 | 2033 |
| claude | 10/10 | 3/3 | 11 | 0 | 0 | 4251 |
| local | 9/10 | 3/3 | 10 | 0 | 2 | 2839 |
| local (sin red) | 9/10 | 3/3 | 11 | 0 | 2 | 2864 |

## Corrección de un dato

| Capa | Sin confirmación | Antiguo no vigente | Recuerda el nuevo | Un hecho |
|---|---|---|---|---|
| nube | ok | ok | ok | ok |
| claude | ok | ok | FALLA | ok |
| local | ok | ok | ok | ok |
| local (sin red) | ok | ok | ok | ok |

## Fallos

- `nube` · ¿Qué hierba no me gusta?: No tengo ese dato. Solo recuerdo que prefieres el té verde antes que el café por las tardes.
- `local` · ¿Qué hierba no me gusta?: No tengo información sobre qué hierbas te gustan o no. Si me dices más, puedo ayudarte a pensar en una respuesta. Pero n
- `local (sin red)` · ¿Qué hierba no me gusta?: No tengo información sobre tus gustos en hierbas. Si me dices más, puedo ayudarte mejor.
