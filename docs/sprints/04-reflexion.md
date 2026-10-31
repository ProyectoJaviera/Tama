# Sprint 4: Reflexión, diario y objetivos

Estado: en curso

## Objetivo
La memoria de Tama se mantiene ordenada sola y empieza a mirar hacia adelante: reflexiona en los ratos de inactividad, escribe un diario y sigue los objetivos de Renzo.

## Alcance
0. Arrastre del S3: prompt más corto. Meta: menos de 2.000 tokens en las 3 capas. Máximo 8 a 10 herramientas visibles por turno (hoy son 17), con un selector por servidor: siempre memoria y tiempo, y el resto según el turno anterior y el estado. Sin regex de intenciones. Se miden de nuevo las evals de herramientas y de memoria, sin regresión.
1. Objetivos (goals). Tabla en memoria.db: id, título, detalle, estado (activo, pausado, logrado o descartado), fecha objetivo, avance (notas con fecha) y entidades relacionadas. Herramientas MCP en el servidor memoria: create_goal, update_goal, list_goals, add_goal_progress. Las acciones de descartar o borrar piden confirmación. Comando /objetivos en la CLI.
2. Diario. Al cerrar el día (o en la primera sesión del día siguiente), se genera memoria/diario/AAAA-MM-DD.md con un resumen breve de lo conversado, lo aprendido y el avance en los objetivos, todo a partir de events.db y del grafo. Lo escribe siempre el modelo local (regla del plan: las tareas de fondo corren en local). Comando /diario (hoy, o una fecha).
3. Reflexión en inactividad. Un planificador simple dentro del proceso (APScheduler o un hilo propio), que arranca tras N minutos sin actividad (configurable, por defecto 20) y solo si hay RAM libre. Siempre en local, sin red. Hace lo siguiente:
   - Unificación de entidades: encuentra candidatas a duplicado (alias, nombres parecidos con FTS5 y distancia de edición) y el modelo local decide si fusionar. Si duda, no fusiona y deja una nota para preguntar.
   - Observaciones contradichas: las marca como no vigentes, sin borrarlas.
   - Propone 0 a 2 "sugerencias pendientes" (por ejemplo, un objetivo sin avance hace 2 semanas) en una tabla suggestions. Tama puede mencionarlas al inicio de la siguiente sesión, con tacto y sin insistir. Esto prepara la fase 3 (acompañante proactivo).
   - Todo queda registrado en events.db, con el tipo reflection. Se puede apagar (TAMA_REFLECTION=false) y se cancela apenas Renzo vuelve a escribir.
4. Export del grafo y los objetivos incluidos en tama memoria export.

## Notas de implementación
- El modelo ve una sola herramienta de objetivos, `manage_goals`, que usa las cuatro de la spec por dentro: en las evals, el modelo local nunca llegó a ellas a través de `abrir_herramientas`.
- `add_observations` sale de la vista para hacerle espacio: `create_entities` ya completa las entidades existentes.

## Pasos de la demo
1. Desde `Tama-s4`, con Ollama abierto: `uv run tama`. Para no esperar 20 minutos, deja `TAMA_REFLECTION_IDLE_MIN=1` en `.env`.
2. Cuéntale una meta con fecha, luego un avance, pregúntale cómo vas y pídele pausarla. Revisa con `/objetivos`.
3. Pídele descartar un objetivo: debe pedir confirmación.
4. Cuéntale dos veces lo mismo de una persona con nombres parecidos y un dato que reemplace a otro (por ejemplo, una mudanza). Espera el minuto sin escribir y revisa con `/memoria`.
5. Escribe `/diario` y revisa que no invente nada.
6. Cierra y vuelve a abrir: si hay una sugerencia pendiente, Tama la menciona una sola vez.
7. Corta internet y repite los pasos 2 y 5.
8. Revisa `tama memoria export`: incluye los objetivos.

## Fuera de alcance
Interfaz web (S5), notificaciones push (ntfy, fase de ubicuidad) y voz.

## Criterio de cierre
- Prompt bajo ~2.000 tokens en las 3 capas, sin regresión en las evals de herramientas y de memoria.
- Evals de reflexión con un grafo sembrado con 5 duplicados y 3 contradicciones (datos ficticios): fusiona al menos 4 duplicados, ninguna fusión incorrecta, y marca las 3 contradicciones.
- Evals de objetivos: crear, actualizar y consultar por conversación natural en las 3 capas.
- El diario de un día de prueba es fiel a events.db (el juez verifica que no invente nada).
- La reflexión no corre mientras Renzo escribe y no carga un segundo modelo.
- Demo de Renzo y, después, una semana de uso real sin duplicados ni datos inventados en el grafo (se revisa con /memoria y el export).
