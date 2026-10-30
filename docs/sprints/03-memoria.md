# Sprint 3: Memoria

Estado: listo para la demo (2026-10-30)

## Objetivo
Tama recuerda a Renzo entre sesiones: un perfil corto siempre presente, un grafo de conocimiento con fechas y la consulta de lo conversado recientemente. Sin base de datos vectorial.

## Cambio respecto del plan
El plan original usaba en el S3 el servidor oficial MCP Memory (Node) y recién en el S4 un grafo propio. Para no meter Node en la imagen y no migrar datos dos veces, el grafo propio en SQLite se hace desde ahora. Usa la misma interfaz de herramientas que el servidor oficial, así que se puede cambiar por él si alguna vez conviene. El S4 queda para la reflexión, el diario, los objetivos y la unificación avanzada.

## Alcance
1. Perfil memoria/nucleo.md. Datos estables de Renzo (nombre, ciudad, intereses, preferencias de trato), de menos de 300 tokens, siempre en el prompt. Lo edita Renzo a mano, o Tama propone un cambio con la herramienta proponer_cambio_perfil, que pide confirmación (s/n) antes de escribir.
2. Servidor MCP memoria (propio, FastMCP, SQLite + FTS5, en data/memoria.db). Herramientas con los nombres del servidor oficial: create_entities, create_relations, add_observations, delete_entities, delete_observations, delete_relations, search_nodes, open_nodes (no exponer read_graph al modelo). Mejoras propias:
   - Cada observación lleva created_at, source (id del evento en events.db) y valid (las observaciones contradichas se marcan como no vigentes, no se borran).
   - Nombres de entidades normalizados (minúsculas, sin tildes) y alias, para no duplicar "Renzo" y "renzo flores".
   - search_nodes usa FTS5 con ranking y devuelve poco: máximo 5 entidades y sus 5 observaciones más recientes y vigentes.
   - Las acciones de borrado llevan destructiveHint y pasan por la confirmación del S2.
3. Recuerdo en cada turno, sin "por si acaso". Al inicio de la sesión, el prompt incluye nucleo.md y un resumen de una línea de la última conversación. Durante la charla, el modelo decide cuándo llamar a search_nodes (regla 3: nada de regex). El contexto total se mantiene bajo ~2.000 tokens.
4. Guardado. El modelo guarda los hechos nuevos con create_entities y add_observations cuando Renzo cuenta algo duradero (gustos, proyectos, personas, planes). La identidad incluye una guía breve sobre qué vale la pena recordar y qué no.
5. Episodios recientes. Herramienta recent_events(dias, consulta) sobre events.db (FTS5 sobre el contenido) para "¿de qué hablamos ayer?".
6. Export versionable. tama memoria export vuelca el grafo a memoria/grafo.jsonl (ordenado y estable) para respaldarlo en git, si Renzo quiere, en un repo privado aparte. Nunca en el repo público.
7. CLI. /memoria muestra las entidades recientes, y /olvidar <entidad> borra con confirmación.
8. Evals de memoria.
   - Guion de un "día 1" con 10 hechos repartidos en una conversación natural. Se reinicia Tama y un "día 2" pregunta por los 10 hechos. Meta: 9 de 10.
   - Se mide cuántas entidades duplicadas o mal nombradas crea cada capa (nube, Claude, local).
   - Casos negativos: no inventar recuerdos y decir "no lo tengo anotado" cuando corresponde.
   - Todo con y sin red.

## Fuera de alcance
Reflexión en inactividad, diario, objetivos (goals) y unificación de entidades con LLM (S4). Interfaz web (S5).

## Criterio de cierre
- Tras reiniciar, Tama recuerda 9 de 10 hechos del día anterior (en las 3 capas, y sin red con la local).
- Duplicados medidos y reportados por capa; ninguna invención en los casos negativos.
- Prompt de la sesión bajo ~2.000 tokens con perfil y recuerdo incluidos.
- Docker: memoria.db y nucleo.md en volúmenes.
- Demo de Renzo.

## Verificación (2026-10-30)

Evals de memoria (`evals/RESULTS_MEMORIA.md`), con datos ficticios:

| Capa | Hechos | Negativos | Entidades | Duplicadas | Tokens de prompt (máx) |
|---|---:|---:|---:|---:|---:|
| nube | 10/10 | 3/3 | 12 | 1 | 2.038 |
| claude | 10/10 | 3/3 | 11 | 0 | 4.251 |
| local | 9/10 | 3/3 | 10 | 0 | 2.578 |
| local sin red | 9/10 | 3/3 | 10 | 0 | 2.415 |

- Sin invenciones en los casos negativos. Meta de 9/10 cumplida en todas las capas.
- Tokens de prompt: la nube está en ~2.000. Local supera la meta (~2.400-2.600) por las 17
  herramientas y el recuerdo automático. El conteo de Claude incluye su costo interno por
  herramientas. Queda como pendiente recortar descripciones si el contexto molesta en local.
- La capa local casi no llama a `search_nodes` por sí sola: ADR 0003 agrega una búsqueda automática
  solo en esa capa (recuerdo sin ella: 2/10; con ella: 9/10).
- Guardar las llamadas a herramientas en el historial subió el guardado: sin eso, los modelos
  dejaban de guardar a mitad de la conversación.
- Sin regresión: herramientas nube 100%, Claude 100%, local 91%; conversación con red 20/20.
- Docker: un hecho guardado en un contenedor se recordó en otro contenedor nuevo; `memoria.db`
  queda en el volumen `tama-data` y el perfil en `tama-memoria`.
- Sin datos personales en el repo: las evals usan datos ficticios, y `memoria/`, `data/` y `*.db`
  están en `.gitignore`.

## Demo para Renzo (unos 15 minutos)

Abrir PowerShell **propio** en `D:\Estudio\IA\Proyecto_Tama\Tama`, en la rama del sprint, con
Ollama encendido.

1. Opcional: crear `memoria\nucleo.md` con tus datos estables (nombre, ciudad, intereses, cómo
   quieres que te trate). Esa carpeta nunca va al repositorio.
2. `uv run tama --check`: tres capas "lista" y los servidores, incluido memoria, "listo".
3. `uv run tama` y contar cosas tuyas en una charla natural: alguien de tu familia, un proyecto,
   algo que te gusta, una fecha. La línea gris muestra `memoria.create_entities`.
4. `/memoria` muestra lo guardado. `/salir`.
5. Abrir Tama de nuevo y preguntar por esas cosas: debe recordarlas (línea gris
   `memoria.search_nodes`). Pregunta también algo que nunca contaste: debe decir que no tiene el
   dato, sin inventar. Prueba "¿de qué hablamos ayer?".
6. Corregir un dato ("ya no vivo ahí, me cambié a...") y volver a preguntar.
7. Cortar la red, esperar unos 40 s y preguntar de nuevo: recuerda con el modelo local.
8. `/olvidar <algo>` pide confirmación. `uv run tama memoria export` crea `memoria\grafo.jsonl`.
