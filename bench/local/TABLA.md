# Comparación de modelos locales (2026-11-02 y 2026-11-03)

Solo CPU (i5-10400F, `num_gpu` 0), un modelo cargado a la vez, sin red. Juez en la nube
(`gemma4:31b-cloud`), nunca Claude. 22 modelos probados. Detalle por eval en
`bench/LOCAL_RESULTS.md`, registros en `bench/local/<modelo>/` y repeticiones con
`uv run python bench/rounds.py`.

El 2026-11-03 se arregló `update_observation` para aceptar las entidades como texto o como mapa
(`{"Pablo": "persona"}`), que es como las mandan los modelos chicos. Las rectificaciones se miden
desde ese arreglo.

## Repeticiones (media y rango)

| Eval | qwen3:4b-instruct-2507 (actual) | ministral-3:8b |
|---|---:|---:|
| Memoria, de 10 (3 rondas) | 7,3 (6 a 9) | **9,0** (8 a 10) |
| Corrección, de 5 (3 rondas) | 3,7 (3 a 5) | **4,7** (4 a 5) |
| Rectificación, de 3 (5 y 2 rondas) | 1,8 (1 a 2) | **3,0** (3 a 3) |
| Herramientas, de 22 (3 rondas) | 19,3 (19 a 20) | 19,7 (19 a 20) |
| Reflexión: duplicados, de 5 | 4,0 (4 a 4) | 4,7 (4 a 5) |
| Reflexión: contradicciones, de 3 | **3,0** (3 a 3) | 1,3 (1 a 2) |

Antes del arreglo, la rectificación del actual daba 0 de 3 en las tres corridas.

## Finalistas (una corrida, salvo lo de arriba)

| | Actual | ministral-3:8b | ornith-1.5:9b | qwen3:8b | gpt-oss:20b |
|---|---:|---:|---:|---:|---:|
| Conversación (20 escenarios, media) | 4,72 | **4,85** | 4,67 | 4,51 | 4,72 |
| Conversación aprobada | 17/20 | **20/20** | 17/20 | 15/20 | 19/20 |
| Respuesta completa (p50) | **9,6 s** | 22,0 s | 37,8 s | 12,6 s | 26,3 s |
| Primer token (p50) | **1,0 s** | **1,0 s** | 6,7 s | 1,6 s | 13,7 s |
| Memoria (de 10) | 9 | 8 | **10** | 8 | 8 |
| Herramientas (de 22) | 20 | 19 | **21** | 20 | 20 |
| Corrección (de 5) | 5 | 5 | 5 | 3 | 2 |
| Historial médico (de 2) | **2** | 0 | — | 0 | **2** |
| Objetivos (de 4) | 2 | 2 | — | 3 | 3 |
| Recordatorios (de 12) | 11 | 12 | 11 | 12 | 12 |
| Reflexión: contradicciones (de 3) | **3** | 1 | **3** | **3** | 0 |
| Diario (juez) | 5/5/5 | 5/5/5 | 5/5/5 | 5/5/5 | 5/5/5 |
| Prueba rápida sin red | 5/5 | 5/5 | — | 5/5 | 5/5 |
| Generación (tok/s) | **8,8** | 4,9 | 5,5 | 5,3 | 6,9 |
| Carga | **3 s** | 26 s | 6 s | 10 s | 23 s |
| RAM del modelo | **3,0 GB** | 5,6 GB | 5,8 GB | 5,5 GB | 12,9 GB |

Ningún finalista hizo fusiones incorrectas en la reflexión, y todos dijeron "no sé" en los 3 casos
de datos nunca contados. qwen2.5:7b también llegó a la suite completa y quedó fuera por la
memoria: 2 de 10.

## Etapa rápida (descartados)

| Modelo | Herramientas (de 22) | Corrección | Rectificación | Generación (tok/s) | Motivo |
|---|---:|---:|---:|---:|---|
| qwen3:4b-instruct-2507-q8_0 | 20 | 4/5 | 3/3 | 6,1 | Igual al actual, pero más lento y con más RAM |
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
