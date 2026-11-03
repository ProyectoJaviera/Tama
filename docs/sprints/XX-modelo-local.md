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

## Resultado del banco (noche del 2026-11-02)
`bench/local_suite.py` corrió 17 modelos sin red y solo en CPU: etapa rápida para 14 y suite
completa para 5. Tabla en `bench/local/TABLA.md` y decisión propuesta en
`docs/adr/0004-modelo-local-final.md`.

- Se mantiene `qwen3:4b-instruct-2507` para el chat y para el fondo: mejor memoria (9/10), 20/22
  herramientas, corrección 5/5, reflexión 4/5 sin fusiones malas y 3/3 contradicciones, y el más
  rápido de los capaces (8,7 tok/s, 3 GB).
- Alternativa: `ministral-3:8b`, el único que rectifica 3/3, pero a 5 tok/s.
- Los MoE de ~18 GB no caben con margen bajo 28 GB de RAM usada.
- Ningún modelo abre grupos de herramientas.

## Pasos de la demo (sin red)
1. Desde `Tama-s7`, con Ollama abierto, desconecta el cable o el WiFi.
2. `uv run tama web` y abre http://127.0.0.1:8765. En Estado debe verse la capa local.
3. Cuéntale algo ("mi hermana se muda a Temuco en marzo"), recarga y pregúntale.
4. Corrige un dato ("en verdad se muda a Valdivia") y pregunta de nuevo.
5. "Recuérdame en 2 minutos regar las plantas" y espera el aviso.
6. Opcional: lo mismo con `TAMA_LOCAL_MODEL=ministral-3:8b` para comparar velocidad.

## Pendiente
- Conversación de 20 escenarios, historial médico y prueba rápida sin red para los finalistas.
- Rectificar un dato que estaba mal sin red: mejorar la descripción de `update_observation`.
