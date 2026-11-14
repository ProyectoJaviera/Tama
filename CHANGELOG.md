# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).

## [Sin publicar]

### Añadido

- Claude como capa principal: orden de la cascada configurable (`TAMA_CASCADE_ORDER`, por defecto
  `claude,nube,local`), gasto contado en cada llamada con la caché, tope mensual con aviso al 80 % y
  gasto en Estado, y caché de prompt automática. Prueba de humo `evals.humo_claude`.
- Capa nube con varias API keys de Ollama Cloud en orden (`OLLAMA_API_KEY_1`, `_2`, `_3`), directo
  a ollama.com: si una agota su cuota se usa la siguiente en la misma respuesta, se reintenta cada
  hora y Estado muestra cada key por número. Sin keys, la capa se apaga. Las evals usan la key 3.
- Voz en la web, tras la demo: un toque en Hablar empieza a escuchar y Tama envía sola cuando
  Renzo hace una pausa (volumen medido en el navegador: umbral calibrado con el ruido de la
  pieza, 1,3 s de silencio tras la voz; sin voz en 8 s deja de escuchar; máximo 60 s). Otro
  toque envía de inmediato; tocar mientras Tama habla la calla y escucha. El micrófono queda
  abierto entre turnos y se cierra tras 2 minutos sin uso. El chat muestra la fecha de cada día.
- Emergencias (131 SAMU y similares): Tama no puede llamar; sin riesgo vital sugiere y pregunta
  antes de actuar; con riesgo vital o si Renzo lo pide, dice de inmediato "llama al 131 ahora" y
  qué hizo. Nunca indica medicamentos. Escenarios `salud-no-vital` y `emergencia-pedida`.
- Evals: juez con Claude cuando la capa está encendida (nunca el modelo local);
  `--rejuzgar-todos`. Arreglado: `--solo` ya no borra los resultados de los demás escenarios.
- Sprint 11 (voz): escuchar con whisper.cpp en CPU y hablar con ElevenLabs (flash) → Piper local,
  con tope mensual de caracteres y cortacircuitos; botón "Hablar" y lectura en voz alta en la web y
  el celular; Estado con la voz activa. Modelos descargados una vez con hash fijo
  (`scripts/descargar_voz.py`). `tama voz disenar/listar/clonar/probar`.
- Sprint 10 (passkey): desde fuera del PC se entra con passkey (WebAuthn con `webauthn` 2.7.0,
  todo local, sin internet). Alta solo con un código de un solo uso del PC (`tama passkey nueva`
  o Estado); `tama passkey listar` y `revocar`. Sesión con cookie HttpOnly, Secure y
  SameSite=Strict, revocable. Desde fuera, las herramientas de archivos y las que borran para
  siempre quedan fuera de la vista del modelo y bloqueadas en el servidor. En 127.0.0.1 nada cambia.
  Editar una nota guarda la versión anterior (`note_versions`).
  Tras la demo: fuera del PC, ante un pedido de borrar, Tama dice de entrada que se hace desde el
  PC y no ofrece borrar.
- Sprint 9 (celular): Tama se instala como app (PWA) con manifest, íconos y un service worker que
  solo guarda la interfaz; diseño usable en pantalla chica. Acceso desde el celular por Tailscale
  (`tailscale serve`, HTTPS) sin abrir puertos: Tama acepta su nombre de Tailscale solo por
  `tailscale serve` y de la cuenta configurada (`TAMA_WEB_TAILSCALE_HOST`,
  `TAMA_WEB_TAILSCALE_USER`) y sigue escuchando en 127.0.0.1. Avisos al celular por el ntfy propio
  vía Tailscale, aviso de prueba en Estado y `/aviso-prueba`, y `tama celular` con los pasos.
  Tras la demo: el rechazo dice la causa y `tama celular` revisa el `.env`; la página se pone al
  día con los avisos al volver del segundo plano; Estado muestra servidor y tópico de ntfy; los
  recordatorios van a ntfy con prioridad alta (`TAMA_NTFY_PRIORITY_RECORDATORIO`).
- Sprint 8 (pulido): la línea de tiempo y las búsquedas dicen "fecha no indicada" en cada dato sin
  fecha, y la línea de tiempo nombra de quién es cada dato; los planes son un tipo de dato propio,
  enlazado a Renzo y con su fecha futura ("el sábado", "en marzo"), y la reflexión guarda los que
  no quedaron guardados; Tama precarga el modelo local al abrirse, lo deja cargado
  (`TAMA_LOCAL_PRELOAD`, `TAMA_LOCAL_KEEP_ALIVE`) y la web dice "preparando" sin red;
  `/memoria` lista las fichas y muestra una entera por páginas, pedida por su nombre, también en
  la web; las sugerencias pendientes van primero; Tama no promete avisos que no puede dar; cada
  pasada de la reflexión queda en events.db. Evals en `python -m evals.pulido`.
  Tras la demo: un plan enviado dos veces en un turno queda una sola vez, como plan, y los
  contadores dicen "1 dato" en singular.
- Modelo local por defecto: `qwen3:4b-instruct-2507-q8_0`, para conversar y para las tareas de fondo (ADR 0004).
  Fechas en palabras, "fecha no indicada" en la línea de tiempo, lo último que dice Renzo gana
  sobre una retractación anterior, precarga al iniciar sin red y el chat web nunca queda
  bloqueado.
- Sprint final: banco del modelo local (`bench/local_suite.py`) con todas las evals sin red y solo
  CPU, comparación de 22 modelos en `bench/local/TABLA.md` (ADR 0004) y repeticiones con
  `bench/rounds.py`.
- El recuerdo automático va junto al último mensaje: el prompt local ya no cambia al inicio y
  Ollama reutiliza lo procesado (primer token de ~1 min a pocos segundos). `TAMA_NUM_THREAD`
  (10 por defecto) usa más hilos de CPU. Las negritas de los modelos se ven en la web.
- `update_observation` acepta las entidades como texto o como mapa, como las mandan los modelos
  locales: la rectificación sin red dejó de fallar siempre.
- Sprint 6: motor proactivo con recordatorios, resumen matutino y seguimiento de objetivos, Silencio
  Operativo, campana en la web, avisos en la CLI y ntfy autoalojado opcional. Comandos
  `/recordatorios`, `/avisos`, `/silencio` y `/resumen`. Se pueden renombrar fichas.
  Tras la demo: franja fija, sonido y notificación del navegador para los recordatorios; los
  urgentes pasan también mientras conversas; Tama dice la hora real si cae en no molestar; estado
  de cada recordatorio en `/recordatorios`, `/resumen` y el panel; botones aplicados marcados;
  comandos guardados en el chat; `/cancelar` por texto o número, con confirmación, y alias
  `/recordatorio`, `/cancela`. El resumen matutino perdido solo sale al iniciar antes de las 12:00. Al editar un
  recordatorio Tama dice la hora exacta, y los botones para elegir vuelven al recargar.
- Sprint 5: interfaz web local con `tama web` (FastAPI, HTMX y Jinja2, sin nada desde internet):
  chat por SSE, confirmación Sí/No, paneles de perfil, memoria, objetivos, diario y estado. El turno
  se comparte con la CLI en `tama/chat.py`. `/memoria <ficha>` muestra todos sus datos.
  Comandos con barra en el chat web, confirmaciones con la nota real y datos erróneos marcados
  como corregidos en la línea de tiempo.
- Sprint 4: objetivos (una herramienta a la vista, `manage_goals`; `discard_goal` con confirmación y
  `/objetivos`), diario en `memoria/diario/` (`/diario`) y reflexión en inactividad con el modelo
  local: fusiona duplicados, marca lo reemplazado (nunca lo que ya ocurrió) y deja hasta 2
  sugerencias. Se apaga con `TAMA_REFLECTION=false`. `tama memoria reparar` ordena las cadenas de
  reemplazos antiguas. Evals en `python -m evals.reflexion`.
- Selector de herramientas (`tama/selector.py`): por turno se ve lo esencial, lo usado en el turno
  anterior y lo de uso común; el resto se pide con `abrir_herramientas`.
- Tras la demo del Sprint 3: `update_observation` corrige un dato sin confirmación y sin borrarlo;
  `/olvidar <tema>` busca por palabras, deja elegir y también limpia los mensajes del tema; las
  confirmaciones se leen en español; la línea gris muestra las capas sin usar y su motivo.
- Memoria con historia: cada hecho se guarda una vez, con un id, y se enlaza a sus entidades;
  fechas absolutas, estados y eventos, `linea_de_tiempo` y `/memoria historial <tema>`. Los datos
  antiguos no se pierden; el borrado real es solo con `/olvidar`.
- `TAMA_CLAUDE_ENABLED`: las evals dejan fuera la capa de Claude salvo con `--con-claude`.
- Sprint 3: memoria entre sesiones. Perfil `memoria/nucleo.md`, servidor MCP de memoria con grafo
  en SQLite (observaciones con fecha, origen y vigencia, alias y búsqueda FTS5), recuerdo de la
  última conversación, `recent_events`, `/memoria`, `/olvidar`, `tama memoria export` y evals de
  memoria (`python -m evals.memoria`).
- Sprint 2: herramientas con servidores MCP locales (tiempo, notas, archivos y web), cliente MCP
  con chequeo de salud y cortacircuitos por servidor, bucle de herramientas de hasta 5 pasos,
  confirmación (s/n) antes de borrar o reemplazar, `/herramientas` y máximo 10 herramientas
  por turno.
- Precarga del modelo local al detectar un corte de red, con el mismo prefijo del próximo turno
  (identidad, herramientas y conversación); opción `TAMA_LOCAL_KEEP_WARM` para mantenerlo en RAM.
- `scripts/vendor.py`: wheels fijadas en `vendor/` para reinstalar sin red.
- Benchmark de herramientas de modelos cloud (`bench/tools_bench.py`) y ADR 0002.
- Evals de herramientas (`python -m evals.tools`), 23 escenarios por capa.

- La línea gris bajo cada respuesta muestra los tokens por segundo de generación, cuando el
  proveedor informa los tokens de salida.

## Sprint 1: conversación (2026-10-30)

Añadido

- Cerebro en cascada (nube de Ollama → Claude → Ollama local) con tiempo máximo al
  primer token, cortacircuitos, detección de conexión y tope de gasto mensual para Claude.
- Interfaz única de proveedores (`tama.providers`) con eventos comunes.
- Identidad de Tama versionada en `src/tama/prompts/identidad.md`.
- Registro de cada turno en `events.db` (SQLite, WAL, migraciones con `PRAGMA user_version`).
- CLI con `/proveedor`, `/salir`, capa en gris bajo cada respuesta y recorte del historial.
- Evals de conversación: 20 escenarios, juez LLM, modos con y sin red y por capa (`--capas`),
  resultados en `evals/RESULTS.md`.
- La nube de Ollama usa la sesión del Ollama local (`ollama signin`); `OLLAMA_API_KEY` es opcional.

## Sprint 0: fundaciones (2026-10-24)

### Añadido

- Esqueleto del repo con `uv`, `ruff`, `pytest` y CI en GitHub Actions (Windows y Linux).
- CLI mínima `tama` que conversa con el modelo local de Ollama en streaming, con chequeo de
  salud al iniciar (`tama --check`).
- `Dockerfile` y `docker-compose.yml`: Tama en un contenedor, Ollama nativo en el host.
- Configuración con `pydantic-settings` y `.env.example`.
- Benchmark de modelos `bench/models.py` (velocidad, RAM y llamadas a herramientas), solo CPU,
  con resultados en `bench/RESULTS.md`.
- ADR 0001: `qwen3:4b-instruct-2507-q4_K_M` como modelo local principal (alternativa `qwen3:8b`) y
  `qwen3:4b` para tareas de fondo.

### Corregido

- El chat y la reflexión no mandaban `keep_alive` a Ollama: volvía a sus 5 minutos y descargaba el
  modelo local aunque la precarga hubiera pedido más.
- Tama siempre envía `num_ctx` (8192) y `num_gpu` 0: no hereda el contexto global de Ollama
  (262.144) ni usa la GPU.
- Con Avast, la variable `SSLKEYLOGFILE` apuntaba a su driver y hacía caer a Python/OpenSSL;
  Tama la quita de su propio proceso.
