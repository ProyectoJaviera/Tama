# Evals de memoria

Generado por `python -m evals.memoria` con datos ficticios. Día 1: 10 hechos en una
conversación natural. Se reinicia Tama. Día 2: una pregunta por hecho, cada una en una
sesión nueva. Casos negativos: datos nunca contados. Meta: 9 de 10 y ninguna invención.

| Capa | Hechos recordados | Negativos | Entidades | Duplicadas | Mal nombradas | Tokens de prompt (máx) |
|---|---:|---:|---:|---:|---:|---:|
| nube | 10/10 | 3/3 | 12 | 1 | 0 | 1727 |
| claude | 10/10 | 3/3 | 11 | 0 | 0 | 4251 |
| local | 9/10 | 3/3 | 15 | 1 | 0 | 2448 |
| local (sin red) | 8/10 | 3/3 | 14 | 0 | 0 | 2295 |

## Historial médico

| Capa | Completo | En orden |
|---|---|---|
| nube | ok | ok |
| local | ok | ok |
| local (sin red) | ok | ok |

## Corrección de un dato

| Capa | Sin confirmación | Antiguo no vigente | Recuerda el nuevo | Un hecho | Anterior |
|---|---|---|---|---|---|
| nube | ok | ok | ok | ok | ok |
| claude | ok | ok | FALLA | ok | FALLA |
| local | ok | FALLA | ok | FALLA | FALLA |
| local (sin red) | ok | FALLA | ok | ok | ok |

## Fallos

- `nube` · duplicada: mama de renzo / renzo
- `local` · ¿Qué hierba no me gusta?: No tengo datos sobre qué hierbas te gustan o no. Si me dices más, puedo ayudarte mejor.
- `local` · duplicada: mama de renzo / renzo
- `local (sin red)` · ¿A qué ciudad se muda mi hermana?: No tengo información sobre la ciudad a la que se muda tu hermana. Si me dices más detalles, puedo ayudarte mejor.
- `local (sin red)` · ¿Qué hierba no me gusta?: No tengo datos sobre tus gustos en hierbas. Si me dices qué hierbas has probado, puedo ayudarte a ver cuál no te gusta.
