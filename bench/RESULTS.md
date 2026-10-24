# Resultados del benchmark de modelos

- Fecha: 2026-10-24
- Equipo: Intel64 Family 6 Model 165 Stepping 3, GenuineIntel, 32 GB RAM, Windows 10, sin GPU
- Ollama 0.40.5 nativo; solo CPU (`num_gpu` 0), temperatura 0, `num_ctx` 4096, thinking apagado
- Prompt largo: ~1485 tokens; herramientas: 10 casos en español (`bench/models.py`)

| Modelo | Prompt (tok/s) | Generación (tok/s) | Carga (s) | RAM (GB) | Herramientas | Latencia media herramientas (s) |
|---|---:|---:|---:|---:|---:|---:|
| qwen3:1.7b | 124.8 | 15.4 | 2.3 | 1.8 | 7/10 (70%) | 2.3 |
| qwen3:4b | 47.4 | 6.7 | 3.8 | 3.0 | 10/10 (100%) | 30.0 |
| qwen3:8b | 28.5 | 4.3 | 11.2 | 5.5 | 10/10 (100%) | 7.2 |
| gemma4:31b-cloud | n/d | n/d | 0.6 | nube | 10/10 (100%) | 0.5 |
| qwen3:4b-instruct-2507-q4_K_M | 46.4 | 6.3 | 4.0 | 3.0 | 9/10 (90%) | 5.0 |

## Fallos de herramientas

- `qwen3:1.7b`: ¿Qué había apuntado sobre la impresora 3D? → no llamó herramienta
- `qwen3:1.7b`: ¿Cuánto es el 15% de 2340? → no llamó herramienta
- `qwen3:1.7b`: Dime la hora, por favor → no llamó herramienta
- `qwen3:4b-instruct-2507-q4_K_M`: ¿Cuánto es el 15% de 2340? → no llamó herramienta
