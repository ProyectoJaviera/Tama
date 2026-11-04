# 0004. Modelo local de la versión: chat y tareas de fondo

- Estado: propuesto (Renzo hizo la demo sin red el 2026-11-03; falta su OK)
- Fecha: 2026-11-03
- Decisores: Renzo
- Reemplaza a: [0001](0001-modelo-local.md)

## Contexto y problema

Al final de la versión se mide el modelo local con todas las tareas de Tama, como pidió Renzo:
memoria, corrección de datos, herramientas, historial, objetivos, recordatorios, conversación,
reflexión y diario. Todo sin red y solo en CPU (i5-10400F, 32 GB, un modelo cargado a la vez).
¿Qué modelo usar para conversar sin internet y cuál para las tareas de fondo?

## Factores de decisión

- Recordar lo que Renzo contó, corregirlo cuando cambia y no inventar: es lo esencial sin red.
- Usar bien las herramientas.
- Reflexión fiable: fusionar duplicados sin fusionar lo distinto y marcar contradicciones.
- Tiempo de respuesta en CPU y RAM con margen: el PC queda en ~11 GB usados sin modelo y pasar
  de ~28 GB arriesga a Windows.
- Un solo modelo evita recargas al pasar de la reflexión al chat.

## Opciones consideradas

22 modelos. Etapa rápida para 19, suite completa para 7 y rondas repetidas de las evals clave
para los tres mejores. Tabla en `bench/local/TABLA.md`.

| | qwen3:4b-instruct-2507 q4_K_M (hoy) | el mismo en q8_0 | ministral-3:8b |
|---|---:|---:|---:|
| Memoria, de 10 | 7,3 (6 a 9; 3 rondas) | 9 (1 ronda) | 9,0 (8 a 10; 3 rondas) |
| Corrección, de 5 (3 rondas) | 3,7 | 3,3 | 4,7 |
| Rectificación, de 3 | 1,8 (5 rondas) | 3,0 (3 rondas) | 3,0 (2 rondas) |
| Herramientas, de 22 | 19,3 | 20 | 19,7 |
| Conversación (20 escenarios) | 4,72; 17 aprobados | 4,75; 18 aprobados | 4,85; 20 aprobados |
| Historial médico, de 2 | 2 | 0 | 0 |
| Reflexión: duplicados, de 5 | 4,0 | 5 | 4,7 |
| Reflexión: contradicciones, de 3 | 3,0 | 3 | 1,3 |
| Respuesta completa (p50) | 9,6 s | 12,4 s | 22,0 s |
| Carga | 3 s | 3 s | 26 s |
| RAM | 3,0 GB | 4,6 GB | 5,6 GB |

ornith-1.5:9b fue el mejor en una corrida (memoria 10, herramientas 21), pero tarda 38 s por
respuesta. Los MoE de ~18 GB (qwen3:30b-a3b, gemma4:26b) no caben con margen bajo 28 GB.
gpt-oss:20b tarda 13,7 s en el primer token y no marca contradicciones.

## Decisión propuesta

- **Un solo modelo para chat y fondo: `qwen3:4b-instruct-2507-q8_0`.** Es el mismo modelo de hoy
  con menos compresión: rectifica 3 de 3 en las tres rondas (el actual, 1,8), recuerda 9 de 10, la
  mejor reflexión (5 de 5 duplicados, 3 de 3 contradicciones) y responde en ~12 s, cerca de los
  ~10 s de hoy. Con un solo modelo no hay recargas.
- **Alternativa, más precisa y más lenta: `ministral-3:8b` para el chat**, dejando el q8_0 en el
  fondo. Corrige mejor (4,7 de 5) y aprueba los 20 escenarios, pero tarda ~22 s por respuesta,
  26 s en cargar y marca pocas contradicciones.
- Tras la demo de Renzo se confirma `qwen3:4b-instruct-2507-q8_0`: con los arreglos pasa el
  historial médico 2 de 2 en tres rondas, y ministral-3:8b lo falla en las tres. ministral además
  tardó más y mostró negritas en crudo.
- **CPU: 10 hilos** (`TAMA_NUM_THREAD`). Ollama usaba solo los 6 núcleos físicos; con 10 lee el
  prompt ~35% más rápido y escribe ~20% más rápido.

## Consecuencias

- Cambia `TAMA_LOCAL_MODEL` y el modelo que se descarga al instalar (4,3 GB en vez de 2,5 GB).
- Si Renzo elige dos modelos, la reflexión y el diario deben pasar a usar `TAMA_BACKGROUND_MODEL`
  (hoy usan el modelo del chat), y Ollama recargará al volver al chat (hasta 26 s).
- El primer turno sin precarga tarda ~80 s (procesa ~3.000 tokens en frío); los siguientes,
  3 a 20 s, porque el inicio del prompt ya no cambia entre turnos.
- La corrección de un dato del q8_0 (3,3 de 5) falla en guardar planes futuros: queda por mejorar
  desde las herramientas.
- Los modelos descartados se pueden borrar para liberar ~110 GB, con el OK de Renzo.
