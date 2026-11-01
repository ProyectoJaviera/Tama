# Evals de Tama

Hasta el último sprint de la versión, el cierre de cada sprint se mide así:

- **Nube, sin Claude.** Es el modo por defecto:
  `python -m evals --capas nube`, `python -m evals.tools`, `python -m evals.memoria` y
  `python -m evals.reflexion objetivos`.
- **Reflexión y diario, en local.** Son tareas de fondo que solo existen ahí:
  `python -m evals.reflexion reflexion` y `python -m evals.reflexion diario`.
- **Prueba rápida sin red.** `python -m evals.smoke`: 5 casos que verifican que Tama responde, no se
  cae y llama herramientas. No tiene metas de calidad.

La calidad del modelo local se mide en el último sprint (`docs/sprints/XX-modelo-local.md`), con
`--local-completo`. La capa de Claude solo se mide con `--con-claude`, para cuidar el cupo de la API.
