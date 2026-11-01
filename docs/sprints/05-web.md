# Sprint 5: Interfaz web local

Estado: en curso

## Objetivo
Renzo conversa con Tama desde el navegador en vez de la CLI, y desde ahí ve y corrige perfil, memoria y objetivos. Todo local y sin depender de internet.

## Alcance
0. Arrastre del S4:
   - Regla en la identidad: no prometer avisos proactivos (llegan en el S6).
   - "¿Tienes alguna sugerencia?" revisa primero las sugerencias pendientes.
   - Las observaciones nuevas dicen de quién son.
   - /memoria <ficha> muestra todos los datos de esa ficha.
1. Servidor: FastAPI + HTMX + Jinja2, sin build ni Node. htmx.min.js y el CSS van en el repo con versión fija; nada desde CDN. Escucha en 127.0.0.1 por defecto, con el puerto en .env y el comando `tama web`. En Docker, el puerto se publica solo en localhost.
2. Chat: streaming por SSE, línea gris con capa, modelo y tok/s, llamadas a herramientas plegables, historial en events.db. El motor se comparte con la CLI, sin lógica duplicada.
3. Confirmación en la web: diálogo Sí/No con texto legible. Nada destructivo corre sin Sí; si se agota el tiempo, cuenta como No.
4. Panel de memoria:
   - Perfil (nucleo.md): ver y editar, con menos de 300 tokens.
   - Fichas: lista con búsqueda FTS5 y detalle con lo vigente y su "antes: …".
   - Línea de tiempo por tema.
   - Editar un dato: crea uno nuevo que reemplaza al anterior, conservando el historial.
   - Olvidar, con confirmación.
   - Fusionar o separar fichas a mano, con confirmación. Renzo nunca se fusiona.
5. Panel de objetivos: lista por estado; crear, editar, agregar avance, pausar, lograr y descartar (con confirmación).
6. Diario por fecha, estado de capas, herramientas y sugerencias pendientes.
7. Seguridad: solo localhost, CSRF en los formularios y escape de todo el contenido de la memoria.
8. Pruebas:
   - Rutas con TestClient.
   - Extremo a extremo con Playwright (chatear, confirmar una acción destructiva, editar un dato), con la Chromium ya instalada.
   - Un test con la red bloqueada que verifica que la página no pide nada a internet.

## Pasos de la demo
1. Desde `Tama-s5`, con Ollama abierto: `uv run tama web`, y abre http://127.0.0.1:8765 en Chrome o Edge. La consola muestra la dirección de inmediato y, unos segundos después, "Tama lista."; mientras tanto, la página dice que está iniciando.
2. Conversa y revisa la línea gris; abre una llamada a herramienta plegada.
3. Pídele borrar una nota: debe aparecer el diálogo Sí/No con texto legible.
4. En Memoria, edita tu perfil, busca una ficha y corrige un dato: debe quedar su "antes: …".
5. En Objetivos, crea uno, agrega un avance, páusalo y descártalo.
6. Revisa Diario y Estado.
7. Corta internet y conversa de nuevo en la web, con la capa local.
8. Cierra y abre `uv run tama`: lo editado debe verse en la CLI.

## Fuera de alcance
Proactivo y ntfy (S6), voz (S7), PWA y remoto (S8 y S9).

## Criterio de cierre
- Chat y confirmación en Chrome o Edge, también sin internet (capa local).
- Editar perfil, dato y objetivo en la web y verlos reflejados en la CLI.
- CI en verde y ningún pedido a internet.
- Docker solo en localhost.
- Demo de Renzo usando la web en lugar de la CLI.
