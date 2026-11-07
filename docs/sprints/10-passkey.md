# Sprint 10: Seguridad remota (passkey)

Estado: cerrado (demo aprobada por Renzo, 2026-11-07)

## Objetivo
Desde fuera del PC solo entra Renzo, con una passkey de su celular, y desde ahí Tama no puede tocar
los archivos del PC ni borrar nada para siempre. En el PC todo sigue igual.

## Alcance
1. Login con passkey (WebAuthn) para todo lo que llega por Tailscale (`tailscale serve`). Librería
   `webauthn` con versión fija (2.7.0), todo en el mismo proceso: funciona sin internet. RP ID = el
   nombre del PC en Tailscale. Sesión con cookie HttpOnly, Secure y SameSite=Strict, de 30 días
   por defecto (`TAMA_WEB_SESSION_DAYS`), revocable.
2. Alta de una passkey: siempre con un código de un solo uso que muestra el PC (`tama passkey
   nueva` o Estado > "Generar código para un celular"), de 8 dígitos, válido 10 minutos y una sola
   vez. Nunca alta abierta desde fuera. La passkey se crea en el celular (es donde vive), con el
   nombre de Tailscale; por eso el código se escribe ahí.
3. Listar y revocar: `tama passkey listar` y `tama passkey revocar <n>`, y en Estado desde el PC.
   Revocar cierra todas las sesiones de esa passkey y la deja registrada (no se borra).
4. Herramientas limitadas fuera del PC: "local" es una petición directa a 127.0.0.1 en el PC; todo
   lo que llega por Tailscale es remoto, aunque el celular esté en el WiFi de la casa. En remoto,
   el modelo no ve estas herramientas y el servidor las rechaza si igual las pide:
   - Archivos del PC: `find_files`, `read_file`, `create_file`, `overwrite_file`.
   - Lo que borra para siempre: `delete_entities`, `delete_observations`, `delete_relations`,
     `forget_memory` (`/olvidar` y "Olvidar" en la ficha), `delete_goal` y `delete_note`.
   Lo que conserva el historial sigue disponible, con la confirmación de siempre: corregir un dato
   (queda su "antes"), editar una nota (ahora guarda la versión anterior), descartar un objetivo,
   cancelar un recordatorio y proponer un cambio al perfil. Tama lo dice con naturalidad: el modelo recibe "solo desde el PC" en la línea de
   herramientas no disponibles, sin otra regla en el prompt.
5. 127.0.0.1 en el PC sigue sin login, como hasta ahora. Pedir passkey en el PC no es posible:
   WebAuthn no acepta una IP como sitio, y el PC ya está protegido por la sesión de Windows. Fuera
   del PC, el login se puede apagar con `TAMA_WEB_LOGIN_REMOTE=false` (no recomendado).
6. Sin sesión, desde fuera solo se sirve lo que no tiene datos: `/sw.js`, el manifest, `/static/`,
   `/entrar`, `/registrar` y `/auth/`. Todo lo demás pide sesión: una página HTML lleva a
   `/entrar`; lo demás responde 401.

## Por qué así
- Bloquear y no pedir confirmación en el PC: una confirmación remota no protege si alguien tiene el
  celular desbloqueado, y borrar para siempre es justo lo que no se puede deshacer. Las acciones
  que conservan historial no pierden datos, así que pueden seguir con su confirmación.
- Editar una nota reemplazaba el texto sin guardarlo; ahora guarda la versión anterior
  (`note_versions`), así que no pierde datos y sigue disponible desde fuera. En la eval, cuando
  estaba bloqueada, el modelo creaba otra nota y decía "actualicé": era peor que permitirla.

## Recuperación si pierdes el celular
1. En el PC: `uv run tama passkey listar` y `uv run tama passkey revocar <n>` (o Estado > Revocar).
   Sus sesiones se cierran al tiro.
2. En la consola de Tailscale, quita ese dispositivo de tu red.
3. Con el celular nuevo: `uv run tama passkey nueva` y regístrala en Tama.

## Eval remota
`python -m evals.remoto`, en la nube y sin Claude: 6/6 en 3 rondas (con una nota que no existe), sin ninguna herramienta
bloqueada ejecutada (antes 4/5: ante "borra todo lo que sabes de mí" ofrecía borrar uno por uno).
La guía remota ahora dice que no ofrezca borrar y qué sí puede hacer: corregir y descartar.

## Fuera de alcance
Voz, varios usuarios y la calidad del modelo local (`13-modelo-local-v2.md`).

## Evals
- Tests: sin passkey no entra desde fuera (303 a /entrar o 401), con sesión sí; código de un solo
  uso, caducado o ausente rechazado; firma de otro sitio rechazada; revocar cierra las sesiones;
  códigos y revocación solo desde el PC; 127.0.0.1 sin cambios; herramientas de archivos y de
  borrado ausentes y bloqueadas en remoto aunque el modelo las pida. Un autenticador de software
  firma como una passkey real para que el servidor verifique de verdad.
- Navegador con autenticador virtual de Chrome: registrar con el código, entrar y conversar.
- Evals sin regresión en la nube, sin Claude.

## Pasos de la demo
Con una memoria de demo aparte (`demo-s10\`): tus datos no se tocan, y las passkeys de la demo
quedan en `demo-s10\data\acceso.db`, separadas de las de tus datos reales. `tailscale serve` y
ntfy siguen como están.

En el PC (PowerShell, desde `D:\Estudio\IA\Proyecto_Tama\Tama-s10`):
1. Cierra la Tama que tengas abierta (Ctrl+C) y abre la de este sprint con la memoria de demo:
   ```powershell
   $env:TAMA_DATA_DIR="demo-s10\data"; $env:TAMA_MEMORY_DIR="demo-s10\memoria"; $env:TAMA_WORKDIR="demo-s10\trabajo"
   uv run tama web
   ```
2. En el PC abre http://127.0.0.1:8765: entra sin login, como siempre. Ve a Estado > "Generar
   código para un celular" (o, en otra ventana, `uv run tama passkey nueva` con las mismas
   variables). Anota el código: vale 10 minutos.

En el celular, con datos móviles y Tailscale conectado:
3. Abre la app Tama: ahora pide entrar. Toca "Registrar esta passkey", escribe el código y un
   nombre, y confirma con tu huella o el bloqueo de pantalla.
4. Toca "Entrar con passkey" y confirma: entras al chat.
5. Pide "¿qué archivos tengo en la carpeta de trabajo?" y "borra la nota del dentista": Tama dice
   que eso se hace desde el PC. Prueba también `/olvidar algo`.

De vuelta en el PC:
6. Estado > Revocar la passkey (o `uv run tama passkey listar` y `uv run tama passkey revocar 1`).
7. En el celular, recarga: vuelve a "Entrar a Tama", y "Entrar con passkey" ya no funciona.
8. Al terminar: cierra Tama y, si quieres, borra `demo-s10\`.

## Criterio de cierre
- Tests y CI en verde.
- Demo de Renzo: registra la passkey del celular, entra con datos móviles, pide leer un archivo y
  Tama se niega, revoca la passkey en el PC y ya no entra.

## Cierre
Evals solo en la nube y sin Claude; ninguna prueba del modelo local.

- Demo real: Renzo registró la passkey del celular con un código del PC, entró con datos móviles,
  Tama se negó a mostrar archivos, y al revocar la passkey en el PC el celular ya no pudo entrar.
- Tras la demo: ante "borra la nota del dentista" (que no existía), Tama la buscaba y no decía
  que borrar se hace desde el PC. La guía remota ahora lo pide de entrada, la encuentre o no. Eval
  remota 6/6 en 3 rondas, sin ninguna herramienta bloqueada ejecutada.
- Regresión: herramientas 23/23, memoria 9/10 y 3/3 sin inventar, conversación 20/20.
- Tests: 493 en verde, con un autenticador de software y una prueba en navegador con el
  autenticador virtual de Chrome. CI en verde.

Pendiente:

- Para usar el celular con los datos reales hay que registrar la passkey otra vez (las de la demo
  quedaron en `demo-s10`).
- El prompt de la nube sigue en ~2.430 tokens (meta ~2.000).
