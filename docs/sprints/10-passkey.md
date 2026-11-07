# Sprint 10: Seguridad remota (passkey)

Estado: en curso

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
   - Lo que borra o reemplaza para siempre: `delete_entities`, `delete_observations`,
     `delete_relations`, `forget_memory` (`/olvidar` y "Olvidar" en la ficha), `delete_goal`,
     `delete_note` y `update_note`.
   Lo que conserva el historial sigue disponible, con la confirmación de siempre: corregir un dato
   (queda su "antes"), descartar un objetivo, cancelar un recordatorio y proponer un cambio al
   perfil. Tama lo dice con naturalidad: el modelo recibe "solo desde el PC" en la línea de
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
- `update_note` queda fuera porque reemplaza el texto anterior sin guardarlo.

## Recuperación si pierdes el celular
1. En el PC: `uv run tama passkey listar` y `uv run tama passkey revocar <n>` (o Estado > Revocar).
   Sus sesiones se cierran al tiro.
2. En la consola de Tailscale, quita ese dispositivo de tu red.
3. Con el celular nuevo: `uv run tama passkey nueva` y regístrala en Tama.

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
(se completan al cierre)

## Criterio de cierre
- Tests y CI en verde.
- Demo de Renzo: registra la passkey del celular, entra con datos móviles, pide leer un archivo y
  Tama se niega, revoca la passkey en el PC y ya no entra.
