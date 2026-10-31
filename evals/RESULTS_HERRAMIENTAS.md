# Evals de herramientas

Generado por `uv run python -m evals.tools`. Servidores MCP reales con datos de ejemplo;
las acciones destructivas nunca se confirman (cuenta como correcto que el modelo pida la
herramienta y el sistema pida confirmación). Criterio de cierre: 90% o más por capa.

| Capa | Correctos | % | Primer token p50 | Voseo |
|---|---:|---:|---:|---:|
| nube | 23/23 | 100% | 0.4 s | 0 |
| claude | 23/23 | 100% | 0.6 s | 0 |
| local | 21/23 | 91% | 2.0 s | 0 |

## Fallos

- `local` · buscar-dentista: faltó search_notes {'query': 'dentista'} (llamó ['search_nodes'])
- `local` · cadena-hora-nota: faltó create_note {'*': 'estudi'} (llamó ['get_current_time'])
