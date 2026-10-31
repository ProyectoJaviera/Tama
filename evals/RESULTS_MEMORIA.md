# Evals de memoria

Generado por `python -m evals.memoria` con datos ficticios. Día 1: 10 hechos en una
conversación natural. Se reinicia Tama. Día 2: una pregunta por hecho, cada una en una
sesión nueva. Casos negativos: datos nunca contados. Meta: 9 de 10 y ninguna invención.

| Capa | Hechos recordados | Negativos | Entidades | Duplicadas | Mal nombradas | Tokens de prompt (máx) |
|---|---:|---:|---:|---:|---:|---:|
| nube | 9/10 | 3/3 | 9 | 1 | 0 | 2198 |
| claude | 10/10 | 3/3 | 11 | 0 | 0 | 4251 |
| local | 9/10 | 3/3 | 10 | 0 | 2 | 3031 |
| local (sin red) | 9/10 | 3/3 | 10 | 0 | 0 | 2839 |

## Historial médico

| Capa | Completo | En orden |
|---|---|---|
| nube | ok | ok |
| local | FALLA | FALLA |
| local (sin red) | FALLA | FALLA |

## Corrección de un dato

| Capa | Sin confirmación | Antiguo no vigente | Recuerda el nuevo | Un hecho | Anterior |
|---|---|---|---|---|---|
| nube | ok | ok | ok | ok | ok |
| claude | ok | ok | FALLA | ok | FALLA |
| local | ok | FALLA | ok | FALLA | FALLA |
| local (sin red) | ok | FALLA | ok | FALLA | FALLA |

## Fallos

- `nube` · ¿Qué hierba no me gusta?: No tengo ese dato. Solo recuerdo que prefieres el té verde antes que el café en las tardes.
- `nube` · duplicada: mama de renzo / renzo
- `local` · ¿Qué hierba no me gusta?: No tengo datos sobre tus gustos en hierbas. Si me dices qué hierbas conoces o qué sientes al probarlas, puedo ayudarte a
- `local (sin red)` · ¿Qué hierba no me gusta?: No tengo datos sobre tus gustos en hierbas. Si me dices más, puedo ayudarte mejor.
