# Banco del modelo local

Generado por `bench/local_suite.py`: CPU sola (num_gpu 0), un modelo a la vez, sin red;
juez en la nube sin Claude. Registros en `bench/local/<modelo>/`.

## Rendimiento

| Modelo | Carga | Prompt tok/s | Generación tok/s | RAM GB | Herramientas (10) |
|---|---:|---:|---:|---:|---:|
| qwen3:30b-a3b-instruct-2507-q4_K_M | 32.2 s | 55.0 | 10.4 | 17.7 | 10/10 |
| qwen3:4b-instruct-2507-q4_K_M | 5.4 s | 52.1 | 6.6 | 3.0 | 9/10 |

## Evals sin red

| Modelo | Memoria | Negativos | Tokens máx | Corrección | Historial | Objetivos | Rectificación | Herramientas | Abre grupos | Recordatorios | Conversación | 1er token p50 | Reflexión | Fusiones malas | Contradicciones | Diario | Sin red | Horas |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| qwen3:30b-a3b-instruct-2507-q4_K_M | - | - | - | 5/5 | - | - | RAM sobre 28 GB | 15/22 | 0 | - | - | - | - | - | - | - | - | 0.2 |
| qwen3:4b-instruct-2507-q4_K_M | 9/10 | 3/3 | 2961 | 5/5 | - | - | 0/3 | 20/22 | 0 | - | - | - | - | - | - | - | - | 0.7 |
