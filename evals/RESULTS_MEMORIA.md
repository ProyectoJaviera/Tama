# Evals de memoria

Generado por `python -m evals.memoria` con datos ficticios. Día 1: 10 hechos en una
conversación natural. Se reinicia Tama. Día 2: una pregunta por hecho, cada una en una
sesión nueva. Casos negativos: datos nunca contados. Meta: 9 de 10 y ninguna invención.

| Capa | Hechos recordados | Negativos | Entidades | Duplicadas | Mal nombradas | Tokens de prompt (máx) |
|---|---:|---:|---:|---:|---:|---:|
| nube | 9/10 | 3/3 | 9 | 1 | 0 | 2081 |
| claude | 10/10 | 3/3 | 11 | 0 | 0 | 4251 |
| local | 8/10 | 3/3 | 13 | 0 | 0 | 2880 |
| local (sin red) | 8/10 | 3/3 | 9 | 0 | 0 | 2693 |

## Historial médico

| Capa | Completo | En orden |
|---|---|---|
| nube | ok | ok |
| local | FALLA | FALLA |
| local (sin red) | ok | ok |

## Corrección de un dato

| Capa | Sin confirmación | Antiguo no vigente | Recuerda el nuevo | Un hecho | Anterior |
|---|---|---|---|---|---|
| nube | ok | ok | ok | ok | ok |
| claude | ok | ok | FALLA | ok | FALLA |
| local | ok | FALLA | ok | FALLA | FALLA |
| local (sin red) | ok | FALLA | ok | FALLA | ok |

## Fallos

- `nube` · ¿Qué hierba no me gusta?: No tengo registro de ninguna hierba que no te guste. Solo recuerdo que prefieres el té verde sobre el café en las tardes
- `nube` · duplicada: mama de renzo / renzo
- `local` · ¿Qué hierba no me gusta?: No tengo datos sobre qué hierbas te gustan o no. Si me dices más, puedo ayudarte mejor. ¿O te refieres a algo diferente?
- `local` · ¿Cuál es mi proyecto de este trimestre?: Tama: No tengo el listado completo de proyectos de este trimestre. Si me dices el nombre de un proyecto o un tema, puedo
- `local (sin red)` · ¿Qué hierba no me gusta?: No tengo información sobre tus gustos en hierbas. ¿Puedes decirme más? Por ejemplo, ¿cuál es la hierba que no te gusta y
- `local (sin red)` · ¿Cómo se llama el proyecto en que trabajo con Tomás?: No tengo el nombre del proyecto en que Renzo trabaja con Tomás. Puedo buscarlo si me lo pides.
