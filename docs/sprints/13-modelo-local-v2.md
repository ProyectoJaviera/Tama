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
4. Arranque sin red: `bench/cold_start.py` en frío (tras reiniciar el PC, no solo tras descargar el
   modelo) y `--tibio`. Meta: menos de 30 s desde abrir Tama hasta la primera respuesta. Medición
   previa al arreglo de keep_alive (2026-11-05, solo descargando el modelo): q4_K_M 51 s y q8_0
   78 s, casi todo en la precarga (carga + prompt).
5. Planes en la reflexión, en local: `python -m evals.pulido planes-reflexion --capas local`.

### Del Sprint 9 (celular)
1. Conversar desde el celular (por Tailscale en la casa) con el PC sin internet: responde la capa
   local; medir el tiempo de la primera respuesta en la PWA.

### Del Sprint 11 (voz)
1. Tamaño de whisper: `ggml-base-q5_1` (por defecto, ~0,8 s por frase corta) frente a
   `ggml-small-q5_1` (~3 s), con grabaciones reales de Renzo, no voz sintética. Con voz de Piper,
   base entendió mal 2 de 5 frases y small, 2 a 3: no sirve para decidir.
2. Ida y vuelta hablada sin red (whisper + modelo local + Piper): `bench/voz_ida_vuelta.py` con
   la capa local.

## Reglas
- Las mismas de `XX-modelo-local.md`: sin Claude, modelos descargados solo con el OK de Renzo y
  resultado en un ADR.
