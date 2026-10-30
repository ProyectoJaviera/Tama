# Evals de herramientas

Generado por `uv run python -m evals.tools`. Servidores MCP reales con datos de ejemplo;
las acciones destructivas nunca se confirman (cuenta como correcto que el modelo pida la
herramienta y el sistema pida confirmación). Criterio de cierre: 90% o más por capa.

| Capa | Correctos | % | Primer token p50 |
|---|---:|---:|---:|
| nube | 23/23 | 100% | 0.3 s |
| claude | 23/23 | 100% | 0.7 s |
| local | 21/23 | 91% | 1.2 s |

## Fallos

- `local` · cadena-hora-nota: faltó create_note {'*': 'estudi'} (llamó ['get_current_time'])
- `local` · borrar-dentista: no pidió confirmación para delete_note (llamó ['search_notes'])
