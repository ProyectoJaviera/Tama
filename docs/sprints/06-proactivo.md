# Sprint 6: Acompañante proactivo

Estado: en curso

## Objetivo
Tama avisa, recuerda y sugiere por iniciativa propia en buen momento, sin molestar (Silencio Operativo). Los avisos llegan a la web, a la CLI y, opcionalmente, al celular.

## Alcance
0. Arrastre del S5:
   - Renombrar la ficha cuando se corrige su nombre (Rocky → Toby, con Rocky como alias), por conversación y desde el panel.
   - Levantar de verdad la web en Docker (docker compose up) y hacer un GET a / desde el host.
   - Reemplazar la regla "no prometer avisos" por "solo prometer lo que el motor puede cumplir".
1. Motor proactivo (el planificador del S4):
   - Recordatorios: crear_recordatorio(texto, cuando) con fechas relativas en America/Santiago; listar, editar y cancelar (con confirmación).
   - Resumen matutino configurable (por defecto 8:00): agenda del día, objetivos cercanos o sin avance y una sugerencia; máximo 5 líneas, sin clima.
   - Seguimiento de objetivos sin avance por N días (por defecto 14), con un máximo de una pregunta por objetivo por semana.
   - Las sugerencias de la reflexión, incluidas las contradicciones, se entregan en buen momento.
2. Silencio Operativo:
   - No molestar de 22:00 a 8:00 (configurable), salvo los recordatorios marcados urgentes.
   - Tope de 3 avisos no pedidos por día; los recordatorios pedidos no cuentan.
   - Un aviso ignorado no se repite más de una vez; se respeta "no me recuerdes eso".
   - No avisar mientras Renzo conversa.
   - Registrar en events.db con tipo 'proactive' y motivo.
3. Canales:
   - Web: campana o panel con historial, en vivo por SSE, con botones Listo / Posponer 1 h / No me recuerdes esto.
   - CLI: mostrar entre turnos, nunca mientras escribe.
   - ntfy opcional: autoalojado en el docker compose con imagen fija, accesible en la LAN (funciona sin internet en el mismo WiFi), configurado con TAMA_NTFY_URL y TAMA_NTFY_TOPIC (aleatorio y largo). Si falla, Tama sigue y lo indica en /estado.
4. Comandos /recordatorios, /avisos, /silencio y /resumen (dispara el resumen matutino a mano), y panel Avisos en la web.
5. Bitácora de valor: marcar útil, molesto o ignorado; /avisos resumen semanal.

## Pasos de la demo
1. Desde `Tama-s6`, con Ollama abierto: `uv run tama web`, y abre http://127.0.0.1:8765.
2. Escribe "recuérdame en 2 minutos sacar la ropa" y espera sin escribir: debe aparecer el aviso y el número en Avisos.
3. En Avisos, prueba Posponer, Listo, Útil y No me recuerdes esto.
4. Escribe `/resumen`, `/recordatorios` y `/avisos resumen`.
5. Pídele cancelar un recordatorio: debe confirmar.
6. Renombra una ficha desde Memoria (Rocky → Toby), o díselo a Tama.
7. Opcional: ntfy en el celular, según el README.

## Notas de implementación
- El resumen matutino no usa el modelo: arma sus líneas con lo guardado, así que no puede inventar.
- Los recordatorios entienden fechas con reglas fijas; si no entienden, le piden a Tama que pregunte.

## Fuera de alcance
Voz, remoto/PWA y clima o noticias.

## Evals
- Tests con reloj simulado (no molestar, tope, ignorados, posponer, urgentes).
- Más de 10 recordatorios en lenguaje natural en la nube.
- El resumen matutino no inventa nada (juez con datos sembrados).

## Criterio de cierre
- Tests en verde.
- Demo: recordatorio a 2 minutos que llega a la web (y al celular con ntfy); /resumen.
- Docker web probada.
- La semana de uso real con la bitácora se revisa en el sprint final (no bloquea el merge).
