# Último sprint de la versión: modelo local

Estado: pendiente. Por decisión de Renzo, estas pruebas se hacen al final de la versión, para elegir el mejor modelo para todas las tareas. Número definitivo por asignar.

## Objetivo
Elegir el modelo local (o los modelos, por tarea) con datos, en CPU y RAM (i5-10400F, 32 GB, sin GPU), un modelo cargado a la vez y sin internet.

## Pruebas por modelo candidato
1. Memoria sin red: `python -m evals.memoria --capas local --offline`. Hoy 7 a 9 de 10, y el modelo a veces no busca al preguntar.
2. Objetivos sin red: `python -m evals.reflexion objetivos --capas local --offline`. Hoy solo crear es fiable; fallan avance, consulta y actualización.
3. Corrección de datos: `python -m evals.memoria --capas local --offline --correccion`. Hoy puede dejar datos antiguos vigentes.
4. Tamaño del prompt: tokens máximos por capa. Hoy hasta 2.892 en local; la meta es ~2.000.
5. Apertura de grupos: si el modelo usa `abrir_herramientas`. Hoy casi no lo hace, y por eso hay 15 herramientas a la vista en vez de 8 a 10. Con un modelo que sí abra grupos, bajar `TAMA_TOOLS_MAX` y volver a medir.
6. Historial médico: `python -m evals.memoria --capas local --offline --historial`, incluido el evento contado días después.
7. Reflexión: `python -m evals.reflexion reflexion`. Meta: al menos 4 de 5 duplicados, 0 fusiones incorrectas y 3 de 3 contradicciones.
8. Diario: `python -m evals.reflexion diario`, sin inventar nada.
9. Herramientas: `python -m evals.tools --capas local`, con al menos 90%.
10. Conversación: `python -m evals --offline`, en español de Chile y sin voseo.
11. Rendimiento: primer token, tokens por segundo, RAM y tiempo de carga en CPU.

## Reglas
- Los modelos se descargan solo con aprobación de Renzo y al instalar, nunca en ejecución.
- Las evals no usan la capa de Claude.
- El resultado queda en un ADR, junto a `docs/adr/0001-modelo-local.md`.
