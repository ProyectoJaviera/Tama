# Comparación de modelos locales (noche del 2026-11-02)

Solo CPU (i5-10400F, `num_gpu` 0), un modelo cargado a la vez, sin red. Juez en la nube
(`gemma4:31b-cloud`), nunca Claude. Una corrida por eval: diferencias de 1 caso son ruido.
Detalle por eval en `bench/LOCAL_RESULTS.md` y registros en `bench/local/<modelo>/`.

## Finalistas (suite completa)

| | qwen3:4b-instruct-2507 (actual) | qwen3:8b | ministral-3:8b | gpt-oss:20b | qwen2.5:7b |
|---|---:|---:|---:|---:|---:|
| Memoria día 2 (de 10) | **9** | 8 | 8 | 8 | 2 |
| No inventa (de 3) | 3 | 3 | 3 | 3 | 3 |
| Herramientas (de 22) | **20** | **20** | 19 | **20** | 19 |
| Corrección de un dato (de 5) | **5** | 3 | **5** | 2 | 2 |
| Rectificación, dato que estaba mal (de 3) | 0 | 0 | **3** | **3** | 2 |
| Objetivos (de 4) | 2 | 3 | 2 | 3 | 3 |
| Recordatorios (de 12) | 11 | 12 | 12 | 12 | 12 |
| Reflexión: duplicados (de 5) | 4 | 4 | **5** | 4 | 2 |
| Reflexión: fusiones incorrectas | 0 | 0 | 0 | 0 | 0 |
| Reflexión: contradicciones (de 3) | **3** | **3** | 1 | 0 | 1 |
| Diario (juez) | 5/5/5 | 5/5/5 | 5/5/5 | 5/5/5 | 5/3/5 |
| Generación (tok/s) | **8,7** | 5,1 | 5,0 | 7,3 | 6,3 |
| Prompt (tok/s) | **72,8** | 38,6 | 52,0 | 45,9 | 46,2 |
| Carga | **5 s** | 9 s | 24 s | 21 s | 9 s |
| RAM del modelo | **3,0 GB** | 5,5 GB | 5,6 GB | 12,9 GB | 4,7 GB |
| Prompt máximo (tokens) | 2.961 | 2.558 | 2.547 | 2.026 | 2.290 |

gpt-oss:20b razona siempre antes de responder: su primer token en herramientas tarda 8,6 s
(p50), contra 1,9 s del actual.

## Etapa rápida (descartados)

| Modelo | Herramientas (de 22) | Corrección | Rectificación | Generación (tok/s) | RAM | Motivo |
|---|---:|---:|---:|---:|---:|---|
| qwen3:30b-a3b-instruct-2507 | 15 (13 con contexto 4096) | 5/5 | 1/3 | 10,4 | 17,7 GB | Roza los 28 GB de RAM usada y rinde peor con herramientas |
| gemma4:26b | — | — | — | 9,0 | 18 GB | Pasa de 28 GB de RAM usada aun con contexto 4096 |
| gemma4:e4b | 17 | 4/5 | 0/3 | 10,8 | 6,6 GB | Peor con herramientas |
| granite4:tiny-h | 17 | 2/5 | 0/3 | 24,9 | 4,2 GB | Rápido, pero corrige mal |
| llama3.2:3b | 15 | 3/5 | 0/3 | 11,5 | 2,4 GB | Herramientas y corrección débiles |
| llama3.1:8b | 14 | 2/5 | 0/3 | 5,7 | 5,2 GB | Herramientas débiles y lento |
| phi4-mini | 5 | 3/5 | 0/3 | 10,1 | 2,9 GB | Casi no usa herramientas |

Solo rendimiento: qwen3:14b (3,1 tok/s, 9,4 GB), qwen3:4b (8,6 tok/s; razona siempre),
qwen3:1.7b (18,9 tok/s; 7 de 10 herramientas en el banco corto), gemma4:31b (2,7 tok/s, 15,2 GB)
y gemma2:2b (Ollama no le da herramientas).

## Lo que no se midió

- Conversación de 20 escenarios sin red, historial médico y la prueba rápida sin red: no cupieron
  en la noche para los finalistas.
- Ningún modelo usó `abrir_herramientas` (0 en todos), así que `TAMA_TOOLS_MAX` queda como está.
