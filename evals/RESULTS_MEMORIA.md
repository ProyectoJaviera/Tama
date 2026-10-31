# Evals de memoria

Generado por `python -m evals.memoria` con datos ficticios. Día 1: 10 hechos en una
conversación natural. Se reinicia Tama. Día 2: una pregunta por hecho, cada una en una
sesión nueva. Casos negativos: datos nunca contados. Meta: 9 de 10 y ninguna invención.

| Capa | Hechos recordados | Negativos | Entidades | Duplicadas | Mal nombradas | Tokens de prompt (máx) |
|---|---:|---:|---:|---:|---:|---:|
| nube | 10/10 | 3/3 | 12 | 1 | 0 | 2038 |
| claude | 10/10 | 3/3 | 11 | 0 | 0 | 4251 |
| local | 9/10 | 3/3 | 10 | 0 | 1 | 2578 |
| local (sin red) | 9/10 | 3/3 | 10 | 0 | 0 | 2415 |

## Corrección de un dato

| Capa | Sin confirmación | Antiguo no vigente | Recuerda el nuevo |
|---|---|---|---|
| nube | ok | ok | ok |
| claude | ok | ok | ok |
| local | ok | FALLA | ok |

## Fallos

- `nube` · duplicada: lucia / mudanza de lucia
- `local` · ¿Qué hierba no me gusta?: No tengo información sobre tus gustos en hierbas. Si me dices qué hierbas has probado, puedo ayudarte a ver si hay algun
- `local (sin red)` · ¿Qué hierba no me gusta?: No tengo datos sobre tus preferencias de hierbas. ¿Querés que lo investiguemos juntos? O tal vez me digas qué hierbas ya
