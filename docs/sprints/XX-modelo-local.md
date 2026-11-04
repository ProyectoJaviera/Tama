# Último sprint de la versión: modelo local

Estado: en curso (banco hecho; falta la demo sin red). Por decisión de Renzo, estas pruebas se hacen al final de la versión, para elegir el mejor modelo para todas las tareas. Va después del S6 y antes de la pausa de uso real.

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
- Hasta este sprint, las evals de cierre van solo en la nube, con reflexión y diario en local y `python -m evals.smoke` sin red. Aquí se usa `--local-completo`.
- Los modelos se descargan solo con aprobación de Renzo y al instalar, nunca en ejecución.
- Las evals no usan la capa de Claude.
- El resultado queda en un ADR, junto a `docs/adr/0001-modelo-local.md`.

## Resultado del banco (2026-11-02 y 2026-11-03)
`bench/local_suite.py` probó 22 modelos sin red y solo en CPU; los tres mejores con rondas
repetidas (`bench/rounds.py`). Tabla en `bench/local/TABLA.md` y decisión propuesta en
`docs/adr/0004-modelo-local-final.md`.

- `update_observation` acepta las entidades como texto o mapa: la rectificación del actual pasó
  de 0 a 1,8 de 3, y la del mismo modelo en q8_0, a 3 de 3.
- Propuesta: `qwen3:4b-instruct-2507-q8_0` para todo (memoria 9, rectificación 3 de 3, la mejor
  reflexión, ~12 s por respuesta). Alternativa: `ministral-3:8b` para el chat (corrige mejor,
  ~22 s por respuesta).
- Los MoE de ~18 GB no caben con margen bajo 28 GB de RAM usada. Ningún modelo abre grupos.

## Tras la demo de Renzo (2026-11-03)
Renzo probó q8_0 y ministral-3:8b sin red. Arreglos: el recuerdo automático ya no cambia el
inicio del prompt (primer token de 55-80 s a 3-20 s), 10 hilos de CPU (`TAMA_NUM_THREAD`), los
datos de salud nuevos se guardan aunque no haya nada parecido, la corrección dice lo que ahora vale
y las negritas se ven bien en la web. q8_0 pasa el historial médico 2 de 2 en tres rondas;
ministral-3:8b lo falla en las tres. Detalle en `bench/local/TABLA.md`.

## Pasos de la demo (sin red)
1. Desde `Tama-s7`, con Ollama abierto, desconecta el cable o el WiFi.
2. En PowerShell: `$env:TAMA_LOCAL_MODEL="qwen3:4b-instruct-2507-q8_0"; uv run tama web` y abre
   http://127.0.0.1:8765. Espera a que la página diga que está lista (precarga el modelo).
3. Cuéntale "mi hermana se muda a Temuco en marzo", y luego "en verdad se muda a Valdivia".
4. Cuéntale "hace tres días fui al dentista por una muela" y "el 2 de agosto me fracturé la
   muñeca"; luego pregunta "cuéntame mi historial médico, en orden".
5. "Me operaron de la rodilla" y después "me equivoqué, nunca me operaron de la rodilla".
6. Mira el Administrador de tareas mientras responde: la CPU debería subir cerca del 85%.
7. Fíjate en el primer token de cada respuesta (la línea gris): salvo la primera, debería
   quedar en segundos.

## Pendiente
- Fijar `qwen3:4b-instruct-2507-q8_0` como modelo por defecto e instalarlo, con el OK de Renzo.
- Guardar planes futuros en la corrección (q8_0 3,3 de 5).
- Borrar los modelos descartados (~110 GB), con el OK de Renzo.
