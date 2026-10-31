# Evals de herramientas

Generado por `uv run python -m evals.tools`. Servidores MCP reales con datos de ejemplo;
las acciones destructivas nunca se confirman (cuenta como correcto que el modelo pida la
herramienta y el sistema pida confirmación). Criterio de cierre: 90% o más por capa.

| Capa | Correctos | % | Primer token p50 | Voseo |
|---|---:|---:|---:|---:|
| nube | 22/23 | 96% | 0.4 s | 0 |
| claude | 23/23 | 100% | 0.6 s | 0 |
| local | 20/23 | 87% | 1.6 s | 0 |

## Fallos

- `nube` · web-example: faltó fetch {'url': 'example.com'} (llamó ['get_current_time', 'read_file'])
- `local` · buscar-dentista: faltó search_notes {'query': 'dentista'} (llamó ['search_nodes'])
- `local` · cadena-hora-nota: faltó create_note {'*': 'estudi'} (llamó ['get_current_time'])
- `local` · web-example: faltó fetch {'url': 'example.com'} (llamó nada)
