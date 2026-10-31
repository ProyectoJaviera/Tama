# Evals de herramientas

Generado por `uv run python -m evals.tools`. Servidores MCP reales con datos de ejemplo;
las acciones destructivas nunca se confirman (cuenta como correcto que el modelo pida la
herramienta y el sistema pida confirmación). Criterio de cierre: 90% o más por capa.

| Capa | Correctos | % | Primer token p50 | Voseo |
|---|---:|---:|---:|---:|
| nube | 23/23 | 100% | 0.3 s | 0 |
| claude | 23/23 | 100% | 0.6 s | 0 |
| local | 20/23 | 87% | 1.4 s | 1 |

## Fallos

- `local` · buscar-dentista: faltó search_notes {'query': 'dentista'} (llamó ['search_nodes'])
- `local` · archivo-presupuesto: faltó find_files {'*': 'presupuesto'} (llamó ['search_notes'])
- `local` · cadena-hora-nota: faltó create_note {'*': 'estudi'} (llamó ['get_current_time'])
