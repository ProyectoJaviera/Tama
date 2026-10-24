# Resultados del benchmark de modelos

- Fecha: 2026-10-24
- Equipo: Intel64 Family 6 Model 165 Stepping 3, GenuineIntel, 32 GB RAM, Windows 10, sin GPU
- Ollama 0.40.5 nativo; solo CPU (`num_gpu` 0), temperatura 0, `num_ctx` 4096, thinking apagado
- Prompt largo: ~1482 tokens; herramientas: 10 casos en español (`bench/models.py`)

| Modelo | Prompt (tok/s) | Generación (tok/s) | Carga (s) | RAM (GB) | Herramientas | Latencia media herramientas (s) |
|---|---:|---:|---:|---:|---:|---:|
| qwen3:1.7b | 114.7 | 15.1 | 2.3 | 1.8 | 7/10 (70%) | 2.4 |

## Fallos de herramientas

- `qwen3:1.7b`: ¿Qué había apuntado sobre la impresora 3D? → no llamó herramienta
- `qwen3:1.7b`: ¿Cuánto es el 15% de 2340? → no llamó herramienta
- `qwen3:1.7b`: Dime la hora, por favor → no llamó herramienta
