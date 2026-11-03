# 0004. Modelo local de la versión: chat y tareas de fondo

- Estado: propuesto (falta la demo de Renzo sin red)
- Fecha: 2026-11-03
- Decisores: Renzo
- Reemplaza en parte a: [0001](0001-modelo-local.md) (el modelo de fondo)

## Contexto y problema

Al final de la versión se mide el modelo local con todas las tareas de Tama, como pidió Renzo:
memoria, corrección de datos, herramientas, objetivos, recordatorios, reflexión y diario, sin red
y solo en CPU (i5-10400F, 32 GB, un modelo cargado a la vez). ¿Qué modelo usar para conversar sin
internet y cuál para las tareas de fondo?

## Factores de decisión

- Recordar lo que Renzo contó y no inventar (lo esencial sin red).
- Usar bien las herramientas, incluidas las correcciones de datos.
- Reflexión fiable: fusionar duplicados sin fusionar lo distinto y marcar contradicciones.
- Velocidad en CPU (generación, prompt, carga) y RAM con margen: Renzo cerró programas y el PC
  queda en ~11 GB usados sin modelo; pasar de ~28 GB arriesga a Windows.

## Opciones consideradas

17 modelos. Etapa rápida (herramientas, corrección, rectificación y rendimiento) para 14 y suite
completa para los 5 mejores. Tabla en `bench/local/TABLA.md`.

| | qwen3:4b-instruct-2507 | qwen3:8b | ministral-3:8b | gpt-oss:20b |
|---|---:|---:|---:|---:|
| Memoria (de 10) | 9 | 8 | 8 | 8 |
| Herramientas (de 22) | 20 | 20 | 19 | 20 |
| Corrección (de 5) | 5 | 3 | 5 | 2 |
| Rectificación (de 3) | 0 | 0 | 3 | 3 |
| Reflexión: contradicciones (de 3) | 3 | 3 | 1 | 0 |
| Generación (tok/s) | 8,7 | 5,1 | 5,0 | 7,3 |
| RAM | 3,0 GB | 5,5 GB | 5,6 GB | 12,9 GB |

Los MoE grandes (qwen3:30b-a3b, gemma4:26b) prometían velocidad, pero con ~18 GB rozan o pasan
el límite de RAM y no rindieron mejor. Los modelos de 1,7 a 4B de otras familias (phi4-mini,
llama3.2, granite4, gemma4:e4b) fallan más con herramientas.

## Decisión

- **Chat local: se mantiene `qwen3:4b-instruct-2507-q4_K_M`.** Es el que mejor recuerda (9 de 10),
  empata en herramientas, corrige bien un dato y es el más rápido de los capaces (8,7 tok/s, carga
  en 5 s, 3 GB). Su punto débil es rectificar un dato que estaba mal (0 de 3); se trabaja con la
  descripción de `update_observation`, como piden las reglas, no cambiando de modelo.
- **Fondo (reflexión y diario): `qwen3:4b-instruct-2507-q4_K_M`, el mismo.** 4 de 5 duplicados,
  0 fusiones incorrectas, 3 de 3 contradicciones y diario 5/5/5. Usar el mismo modelo evita
  recargar al pasar de conversar a reflexionar. `qwen3:4b` (fondo en el ADR 0001) razona siempre
  y no aporta.
- **Alternativa: `ministral-3:8b`.** El único que rectifica 3 de 3 y corrige 5 de 5, pero genera
  a 5 tok/s, carga en 24 s y marca solo 1 de 3 contradicciones en la reflexión. Si Renzo prioriza
  las correcciones sobre la velocidad, sirve para el chat con `TAMA_LOCAL_MODEL=ministral-3:8b`,
  dejando el fondo en el 4B.

## Consecuencias

- Positivas: no cambia nada en la instalación; RAM holgada; el chat sin red sigue siendo el más
  rápido posible en este PC.
- Negativas: rectificar datos sin red queda débil hasta mejorar la herramienta.
- Pendiente: conversación de 20 escenarios, historial médico y prueba rápida sin red para los
  finalistas; una corrida por eval, así que diferencias de un caso son ruido.
- Los modelos descartados se pueden borrar para liberar ~90 GB, con el OK de Renzo.
