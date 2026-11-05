# Comparación de modelos locales (2026-11-02 y 2026-11-03)

Solo CPU (i5-10400F, `num_gpu` 0), un modelo cargado a la vez, sin red. Juez en la nube
(`gemma4:31b-cloud`), nunca Claude. 22 modelos probados. Detalle por eval en
`bench/LOCAL_RESULTS.md`, registros en `bench/local/<modelo>/` y repeticiones con
`uv run python bench/rounds.py`.

El 2026-11-03 se arregló `update_observation` para aceptar las entidades como texto o como mapa
(`{"Pablo": "persona"}`), que es como las mandan los modelos chicos. Las rectificaciones se miden
desde ese arreglo.

## Repeticiones (media y rango)

| Eval | Actual (q4_K_M) | Actual en q8_0 | ministral-3:8b |
|---|---:|---:|---:|
| Memoria, de 10 | 7,3 (6 a 9; 3 rondas) | 9 (1 ronda) | **9,0** (8 a 10; 3 rondas) |
| Corrección, de 5 (3 rondas) | 3,7 (3 a 5) | 3,3 (3 a 4) | **4,7** (4 a 5) |
| Rectificación, de 3 | 1,8 (1 a 2; 5 rondas) | **3,0** (3 a 3; 3 rondas) | **3,0** (3 a 3; 2 rondas) |
| Herramientas, de 22 | 19,3 (19 a 20; 3 rondas) | 20 (1 ronda) | 19,7 (19 a 20; 3 rondas) |
| Reflexión: duplicados, de 5 | 4,0 (4 a 4; 3 rondas) | **5** (1 ronda) | 4,7 (4 a 5; 3 rondas) |
| Reflexión: contradicciones, de 3 | **3,0** (3 a 3) | **3** (1 ronda) | 1,3 (1 a 2) |

Antes del arreglo, la rectificación del actual daba 0 de 3 en las tres corridas.

## Finalistas (una corrida, salvo lo de arriba)

| | Actual | Actual q8_0 | ministral-3:8b | ornith-1.5:9b | qwen3:8b | gpt-oss:20b |
|---|---:|---:|---:|---:|---:|---:|
| Conversación (20 escenarios, media) | 4,72 | 4,75 | **4,85** | 4,67 | 4,51 | 4,72 |
| Conversación aprobada | 17/20 | 18/20 | **20/20** | 17/20 | 15/20 | 19/20 |
| Respuesta completa (p50) | **9,6 s** | 12,4 s | 22,0 s | 37,8 s | 12,6 s | 26,3 s |
| Primer token (p50) | **1,0 s** | 1,6 s | **1,0 s** | 6,7 s | 1,6 s | 13,7 s |
| Memoria (de 10) | 9 | 9 | 8 | **10** | 8 | 8 |
| Herramientas (de 22) | 20 | 20 | 19 | **21** | 20 | 20 |
| Corrección (de 5) | 5 | 4 | 5 | 5 | 3 | 2 |
| Historial médico (de 2) | **2** | 0 | 0 | — | 0 | **2** |
| Objetivos (de 4) | 2 | — | 2 | — | 3 | 3 |
| Recordatorios (de 12) | 11 | — | 12 | 11 | 12 | 12 |
| Reflexión: contradicciones (de 3) | **3** | **3** | 1 | **3** | **3** | 0 |
| Diario (juez) | 5/5/5 | — | 5/5/5 | 5/5/5 | 5/5/5 | 5/5/5 |
| Prueba rápida sin red | 5/5 | — | 5/5 | — | 5/5 | 5/5 |
| Generación (tok/s) | **8,8** | 6,1 | 4,9 | 5,5 | 5,3 | 6,9 |
| Carga | 3 s | **3 s** | 26 s | 6 s | 10 s | 23 s |
| RAM del modelo | **3,0 GB** | 4,6 GB | 5,6 GB | 5,8 GB | 5,5 GB | 12,9 GB |

Ningún finalista hizo fusiones incorrectas en la reflexión, y todos dijeron "no sé" en los 3 casos
de datos nunca contados. qwen2.5:7b también llegó a la suite completa y quedó fuera por la
memoria: 2 de 10.

## Tras la demo de Renzo (2026-11-03, noche)

Arreglos, medidos sin red:

- **Primer token.** El recuerdo automático iba dentro del system prompt: cada turno con
  coincidencias cambiaba el inicio del prompt y Ollama recalculaba ~3.000 tokens (55 a 80 s en la
  demo). Ahora va junto al último mensaje. Con q8_0, en una conversación como la demo
  (`bench/first_token.py`), solo el primer turno, sin precarga, tarda 81 s; los demás, 3 a 20 s
  hasta el primer texto.
- **Hilos de CPU.** Ollama usaba los 6 núcleos físicos. `TAMA_NUM_THREAD=10` (por defecto):

| Hilos | q8_0 prompt | q8_0 generación | ministral prompt | ministral generación |
|---:|---:|---:|---:|---:|
| 6 | 29,6 | 5,2 | 31,6 | 4,7 |
| 8 | 33,3 | 6,0 | 35,9 | 5,1 |
| 10 | **41,0** | **6,5** | 39,2 | **5,5** |
| 12 | 40,6 | 6,0 | **40,2** | 5,2 |

- **Datos de salud nuevos.** El modelo pedía la hora para "hace tres días" y no guardaba el dato.
  La descripción de `get_current_time` dice que no hace falta, la identidad pide guardar aunque
  no haya nada parecido, y `update_observation` sin ids manda a `create_entities`.
- **Corrección.** El resultado dice primero lo que ahora vale, para que el modelo no conteste lo
  contrario. Las **negritas** de los modelos se ven como negrita en la web, sin HTML.

Repetición tras los arreglos (3 rondas cada uno):

| | q8_0 | ministral-3:8b |
|---|---:|---:|
| Historial médico, de 2 | **2, 2, 2** | 0, 0, 0 |
| Corrección, de 5 | 3, 3, 4 | 5, 5, 3 |

La corrección de q8_0 falla siempre en lo mismo: no guarda un plan futuro ("me voy a disfrazar
de Shrek"). Queda pendiente.

Segunda demo de Renzo (2026-11-04), y arreglos: las fechas se guardan y muestran en palabras ("1 de
noviembre de 2026"; el modelo leía 2026-11-01 como el 11), sin fecha la línea de tiempo dice "fecha
no indicada", la fecha que el modelo manda aparte (`fecha`) se suma a la frase, la búsqueda aclara
que no cambia nada y el voseo se pasa a tú antes de mostrarse. Con todo eso, q8_0 en 3 rondas:
historial médico 2, 1 y 2 de 2; corrección 3, 4 y 3 de 5. En la ronda que falló, el modelo dijo
"fecha no especificada" para una operación que sí tenía fecha guardada.

Tercera demo (2026-11-04): Renzo dijo "me operaron de la rodilla" sobre una retractación anterior y
Tama respondió lo contrario. `update_observation` ahora sigue un id viejo hasta su versión vigente,
una retractación se puede reemplazar aunque sea de salud, y el resultado dice qué dejó de valer.
Con eso, q8_0 en 3 rondas: corrección 4, 4 y 5 de 5; historial médico 2, 2 y 2; rectificación
3, 3 y 3.

## Etapa rápida (descartados)

| Modelo | Herramientas (de 22) | Corrección | Rectificación | Generación (tok/s) | Motivo |
|---|---:|---:|---:|---:|---|
| nemotron-3.5-bolt:e4b | 18 | 3/5 | 3/3 | 6,5 | Herramientas y corrección más débiles |
| bonsai-2:9b | 18 | 3/5 | 0/3 | 8,1 | Prompt muy lento (14 tok/s) |
| qwen3:30b-a3b-instruct-2507 | 15 | 5/5 | 1/3 | 10,4 | Roza los 28 GB de RAM usada |
| gemma4:26b | — | — | — | 9,0 | Pasa de 28 GB de RAM usada |
| gemma4:e4b | 17 | 4/5 | 0/3 | 10,8 | Herramientas débiles |
| granite4:tiny-h | 17 | 2/5 | 0/3 | 24,9 | Corrige mal |
| llama3.2:3b | 15 | 3/5 | 0/3 | 11,5 | Herramientas débiles |
| llama3.1:8b | 14 | 2/5 | 0/3 | 5,7 | Herramientas débiles y lento |
| phi4-mini | 5 | 3/5 | 0/3 | 10,1 | Casi no usa herramientas |

La rectificación de los modelos de la etapa rápida del 2026-11-02 se midió antes del arreglo.
Solo rendimiento: qwen3:14b (3,1 tok/s), qwen3:4b (razona siempre), qwen3:1.7b, gemma4:31b
(2,7 tok/s) y gemma2:2b (sin herramientas en Ollama). nimble-v3:9b no admite chat en Ollama.
Ningún modelo usó `abrir_herramientas`.
