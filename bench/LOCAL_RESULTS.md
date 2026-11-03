# Banco del modelo local

Generado por `bench/local_suite.py`: CPU sola (num_gpu 0), un modelo a la vez, sin red;
juez en la nube sin Claude. Registros en `bench/local/<modelo>/`.

## Rendimiento

| Modelo | Carga | Prompt tok/s | Generación tok/s | RAM GB | Herramientas (10) |
|---|---:|---:|---:|---:|---:|
| gemma4:26b | 29.1 s | 37.6 | 9.0 | 8.4 | 10/10 |
| gemma4:e4b | 10.9 s | 66.8 | 10.8 | 1.8 | 10/10 |
| gpt-oss:20b | 21.2 s | 45.9 | 7.3 | 12.9 | 9/10 |
| granite4:tiny-h | 0.1 s | 128.8 | 24.9 | 4.1 | 9/10 |
| llama3.1:8b | 8.8 s | 40.7 | 5.7 | 5.2 | 9/10 |
| llama3.2:3b | 4.1 s | 102.4 | 11.5 | 2.4 | 7/10 |
| ministral-3:8b | 24.3 s | 52.0 | 5.0 | 5.6 | 10/10 |
| phi4-mini:latest | 4.7 s | 88.9 | 10.1 | 2.9 | 2/10 |
| qwen2.5:7b | 8.7 s | 46.2 | 6.3 | 4.7 | 10/10 |
| qwen3:30b-a3b-instruct-2507-q4_K_M | 31.4 s | 55.2 | 10.4 | 17.7 | 10/10 |
| qwen3:4b-instruct-2507-q4_K_M | 5.0 s | 72.8 | 8.7 | 3.0 | 9/10 |
| qwen3:8b | 9.4 s | 39.3 | 5.2 | 5.5 | 10/10 |

## Evals sin red

| Modelo | Memoria | Negativos | Tokens máx | Corrección | Historial | Objetivos | Rectificación | Herramientas | Abre grupos | Recordatorios | Conversación | 1er token p50 | Reflexión | Fusiones malas | Contradicciones | Diario | Sin red | Horas |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| gemma4:26b | - | - | - | RAM sobre 28 GB | - | - | RAM sobre 28 GB | RAM sobre 28 GB | RAM sobre 28 GB | - | - | - | - | - | - | - | - | 0.1 |
| gemma4:e4b | - | - | - | 4/5 | - | - | 0/3 | 17/22 | 0 | - | - | - | - | - | - | - | - | 0.2 |
| gpt-oss:20b | 8/10 | 3/3 | 2026 | 2/5 | - | 3/4 | 3/3 | 20/22 | 0 | 12/12 | - | - | 4/5 | [] | 0/3 | [5, 5, 5] | - | 1.8 |
| granite4:tiny-h | - | - | - | 2/5 | - | - | 0/3 | 17/22 | 0 | - | - | - | - | - | - | - | - | 0.2 |
| llama3.1:8b | - | - | - | 2/5 | - | - | 0/3 | 14/22 | 0 | - | - | - | - | - | - | - | - | 0.6 |
| llama3.2:3b | - | - | - | 3/5 | - | - | 0/3 | 15/22 | 0 | - | - | - | - | - | - | - | - | 0.3 |
| ministral-3:8b | 8/10 | 3/3 | 2547 | 5/5 | - | 2/4 | 3/3 | 19/22 | 0 | 12/12 | - | - | 5/5 | [] | 1/3 | [5, 5, 5] | - | 1.6 |
| phi4-mini:latest | - | - | - | 3/5 | - | - | 0/3 | 5/22 | 0 | - | - | - | - | - | - | - | - | 0.1 |
| qwen2.5:7b | 2/10 | 3/3 | 2290 | 2/5 | - | 3/4 | 2/3 | 19/22 | 0 | 12/12 | - | - | 2/5 | [] | 1/3 | [5, 3, 5] | - | 0.5 |
| qwen3:30b-a3b-instruct-2507-q4_K_M | - | - | - | 5/5 | - | - | 1/3 | 13/22 | 0 | - | - | - | - | - | - | - | - | 0.3 |
| qwen3:4b-instruct-2507-q4_K_M | 9/10 | 3/3 | 2961 | 5/5 | - | 2/4 | 0/3 | 20/22 | 0 | 11/12 | - | - | 4/5 | [] | 3/3 | [5, 5, 5] | - | 0.8 |
| qwen3:8b | - | - | - | 3/5 | - | - | 0/3 | 20/22 | 0 | - | - | - | - | - | - | - | - | 0.3 |
