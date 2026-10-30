# Herramientas: modelos cloud de Ollama con muchas herramientas a la vez

- Generado: 2026-10-29 con `bench/tools_bench.py`, vía el Ollama local
  (sesión de `ollama signin`). Las herramientas no se ejecutan: devuelven resultados de
  ejemplo. Escalones: 5, 10, 20 y 40 herramientas visibles (las reales más relleno
  plausible, en orden mezclado). Por escalón, todos los casos cuyas herramientas están
  a la vista (21 en el escalón de 5, 29 en los demás): herramienta correcta, argumentos,
  cadenas de 2-3 llamadas y no llamar cuando no corresponde.

| Modelo | 5 | 10 | 20 | 40 | Latencia media (s) | Errores de formato | Errores API |
|---|---:|---:|---:|---:|---:|---:|---:|
| gemma4:31b-cloud | — | — | — | 93% (27/29) | 0.6 | 0 | 0 |
| gpt-oss:120b-cloud | — | — | — | 86% (25/29) | 0.7 | 0 | 0 |
| gpt-oss:20b-cloud | — | — | — | 90% (26/29) | 1.2 | 0 | 0 |
| deepseek-v4-pro:cloud | — | — | — | — | — | — | — |

## Fallos por modelo y escalón

- `gemma4:31b-cloud` (40): nota-filamento: llamó todo_add; nota-banco: llamó todo_add
- `gpt-oss:120b-cloud` (40): editar-nota: llamó nada; borrar-nota: llamó nada; reemplazar: llamó nada; cadena-viaje: llamó search_notes, find_files, find_files, read_file
- `gpt-oss:20b-cloud` (40): borrar-nota: llamó nada; archivo-presupuesto: llamó search_notes; reemplazar: llamó nada
