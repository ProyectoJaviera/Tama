# Sprint 13: Modelo local de la v2

Estado: abierto (se llena durante la v2; se ejecuta al final de la versión)

Como en `XX-modelo-local.md`, la calidad del modelo local se mide al final de la versión, con
todas las tareas ya hechas. Aquí se anotan, sprint a sprint, las pruebas sin red que se dejan para
entonces.

## Pruebas pendientes

### Del Sprint 8 (pulido)
1. Fecha no indicada: `python -m evals.pulido fechas --capas local --offline`. El historial con un
   evento sin fecha debe decir "fecha no indicada" para ese evento.
2. Planes futuros: `python -m evals.pulido planes --capas local --offline`. El modelo debe llamar
   `guardar_plan` con la fecha si la hay.
3. Sugerencias y avisos: `python -m evals.pulido sugerencias avisos --capas local --offline`.
4. Arranque sin red, en frío tras reiniciar el PC (no solo tras descargar el modelo):
   `bench/cold_start.py`, meta menos de 30 s hasta la primera respuesta.

## Reglas
- Las mismas de `XX-modelo-local.md`: sin Claude, modelos descargados solo con el OK de Renzo y
  resultado en un ADR.
