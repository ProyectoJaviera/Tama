# Evals de memoria

Generado por `python -m evals.memoria` con datos ficticios. Día 1: 10 hechos en una
conversación natural. Se reinicia Tama. Día 2: una pregunta por hecho, cada una en una
sesión nueva. Casos negativos: datos nunca contados. Meta: 9 de 10 y ninguna invención.

| Capa | Hechos recordados | Negativos | Entidades | Duplicadas | Mal nombradas | Tokens de prompt (máx) |
|---|---:|---:|---:|---:|---:|---:|
| nube | 9/10 | 3/3 | 9 | 1 | 0 | 2188 |
| claude | 10/10 | 3/3 | 11 | 0 | 0 | 4251 |
| local | 8/10 | 3/3 | 14 | 0 | 0 | 2892 |
| local (sin red) | 7/10 | 3/3 | 10 | 0 | 0 | 2721 |

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
| local | ok | FALLA | ok | FALLA | ok |
| local (sin red) | ok | FALLA | ok | FALLA | FALLA |

## Fallos

- `nube` · ¿Qué hierba no me gusta?: No tengo registro de ninguna hierba que no te guste. Solo recuerdo que prefieres el té verde sobre el café por las tarde
- `nube` · duplicada: mama de renzo / renzo
- `local` · ¿Qué hierba no me gusta?: No tengo información sobre qué hierbas te gustan o no. Si me dices más, puedo ayudarte mejor. ¿Te refieres a una hierba 
- `local` · ¿Cuál es mi proyecto de este trimestre?: No tengo el listado de proyectos de este trimestre. Puedes decirme más sobre qué tipo de proyecto te interesa o cuál es 
- `local (sin red)` · ¿Qué hierba no me gusta?: No tengo datos sobre tus gustos en hierbas. Si me dices más, puedo ayudarte mejor. ¿Te refieres a una hierba en cocina o
- `local (sin red)` · ¿Qué libro estoy leyendo?: No tengo información sobre qué libro estás leyendo. Si lo tienes en mente, cuéntamelo y te lo anoto.
- `local (sin red)` · ¿Cuál es mi proyecto de este trimestre?: No tengo datos sobre los proyectos de este trimestre de Renzo. Si me dices más, puedo ayudarte a organizarlo.
