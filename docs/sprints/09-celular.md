# Sprint 9: Tama en el celular

Estado: cerrado (demo aprobada por Renzo, 2026-11-06)

## Objetivo
Renzo usa Tama desde el celular, en la casa o con datos móviles, como una app instalada, y recibe
los avisos ahí. Sin abrir puertos del router y sin que Tama quede a la vista de internet.

## Alcance
1. Acceso remoto con Tailscale, sin abrir puertos: `tailscale serve` en el PC con Windows publica
   Tama (127.0.0.1:8765) en la red privada de Tailscale con HTTPS y certificado válido (lo necesita
   la PWA). Tama sigue en Docker (o con `uv run tama web`), Ollama sigue nativo, y Tama sigue
   respondiendo en 127.0.0.1 igual que antes. Tama acepta el nombre del PC en Tailscale solo si la
   petición llega por `tailscale serve` (que agrega quién la hace) y, si se configura, solo de la
   cuenta de Renzo.
2. PWA instalable: manifest, íconos y un service worker que solo guarda la interfaz (estilos,
   scripts, íconos y una página "sin conexión"); nunca páginas con datos, `/chat`, `/memoria`,
   `/api` ni nada que no sea GET. Diseño usable en pantalla chica: chat, memoria, objetivos y
   avisos.
3. Avisos ntfy en el celular real, con el servidor ntfy propio del docker compose: los avisos no
   pasan por ntfy.sh y llegan por Tailscale también con datos móviles. Tópico largo y aleatorio en
   `.env`. Botón "Enviar aviso de prueba" en Estado y comando `/aviso-prueba`.
4. `tama celular`: muestra los pasos y comandos de Tailscale y ntfy con los valores de este PC y
   propone un tópico aleatorio, sin escribir nada.
5. Seguridad mientras no exista el login del S10: el acceso queda limitado a los dispositivos de la
   cuenta Tailscale de Renzo; ningún puerto queda abierto a internet. Documentado en el README.

## Por qué ntfy propio y no ntfy.sh
El texto de los avisos (recordatorios, objetivos, salud) es personal: con el servidor propio nunca
sale del PC ni de la red de Tailscale, y en la casa funciona aunque se caiga internet. ntfy.sh
sería más simple, pero cualquiera que adivine el tópico lee los avisos y el texto queda en un
tercero. En iPhone, ntfy necesita a ntfy.sh solo para despertar la app (`upstream-base-url`): se
envía un identificador del mensaje, no su texto.

## Prioridad de los avisos en ntfy
Un recordatorio lo pidió Renzo para una hora: va con prioridad alta (`high`), con sonido, emergente
y en la pantalla de bloqueo. Uno urgente va al máximo (`urgent`). Sugerencias, resumen matutino y
seguimiento de objetivos pueden esperar: prioridad normal, para no molestar. La de los
recordatorios se cambia con `TAMA_NTFY_PRIORITY_RECORDATORIO`. El aviso de prueba usa la misma, así
muestra cómo llegará un recordatorio.

En el celular (Samsung/Android), si no suenan o no salen bloqueado: en ntfy, la suscripción > ⋮ >
Ajustes de notificación (prioridad mínima "Por defecto" o menor y sonido); en Ajustes >
Notificaciones > Notificaciones en pantalla de bloqueo, "Mostrar contenido" con ntfy permitida; y
batería "Sin restricciones" para ntfy y Tailscale. Estado lo recuerda.

## Fuera de alcance
Login y usuarios (S10), voz y cambios al modelo local (las pruebas del modelo local van en
`13-modelo-local-v2.md`).

## Evals
- Tests: manifest e íconos se sirven; el service worker se sirve en la raíz y no guarda páginas con
  datos, `/chat`, `/api` ni nada que no sea GET; el nombre de Tailscale solo se acepta con la
  cabecera de `tailscale serve` (y de la cuenta indicada); 127.0.0.1 sigue igual; aviso de prueba
  por ntfy con servidor simulado.
- Prueba en Chromium con pantalla de celular (Playwright): chat, memoria, objetivos y avisos sin
  desborde horizontal; el service worker se registra.
- Evals sin regresión en la nube, sin Claude: herramientas, memoria y conversación.

## Pasos de la demo
Con una memoria de demo aparte (`demo-s9\`): tus datos no se tocan. Todo en PowerShell, desde
`D:\Estudio\IA\Proyecto_Tama\Tama-s9`.

En el PC:
1. Instala Tailscale (https://tailscale.com/download/windows) e inicia sesión. En
   https://login.tailscale.com/admin/dns activa MagicDNS y HTTPS Certificates. Si el primer `tailscale serve` pide activar HTTPS, deja marcado HTTPS certificates y desmarca Tailscale Funnel: Funnel publicaría Tama a todo internet.
2. `uv run tama celular`: muestra el nombre del PC en Tailscale y las líneas para el `.env`. Copia
   a tu `.env` las de `TAMA_WEB_TAILSCALE_HOST`, `TAMA_WEB_TAILSCALE_USER` (tu correo de
   Tailscale), `TAMA_NTFY_URL`, `TAMA_NTFY_TOPIC` y `TAMA_NTFY_PUBLIC_URL`. Si tu celular es un
   iPhone, agrega también `TAMA_NTFY_UPSTREAM=https://ntfy.sh`.
3. Publica Tama y ntfy dentro de Tailscale y levanta ntfy:
   ```powershell
   tailscale serve --bg --https=443 http://127.0.0.1:8765
   tailscale serve --bg --https=8443 http://127.0.0.1:8090
   docker compose --profile ntfy up -d ntfy
   ```
4. Abre Tama con la memoria de demo:
   ```powershell
   $env:TAMA_DATA_DIR="demo-s9\data"; $env:TAMA_MEMORY_DIR="demo-s9\memoria"; $env:TAMA_WORKDIR="demo-s9\trabajo"
   uv run tama web
   ```
   En el PC, http://127.0.0.1:8765 sigue funcionando igual que antes.

En el celular, con el WiFi apagado (solo datos móviles):
5. Instala la app Tailscale, entra con la misma cuenta y conéctate.
6. Abre `https://<nombre del PC>` (el que mostró `tama celular`). Instálala: en Chrome, menú >
   "Agregar a la pantalla principal"; en Safari, Compartir > "Agregar a inicio". Ábrela desde el
   ícono del huevo.
7. Conversa: "hola Tama" y "recuérdame en 2 minutos tomar agua". Revisa Memoria, Objetivos y
   Avisos: todo debe caber en la pantalla.
8. Instala la app ntfy, agrega el servidor `https://<nombre del PC>:8443` y suscríbete al tópico de
   `TAMA_NTFY_TOPIC`. En Tama, Estado > "Enviar aviso de prueba": debe llegar en segundos. A los 2
   minutos llega también el recordatorio del agua.
9. Al terminar: `tailscale serve reset` deja de publicar Tama (opcional) y puedes borrar `demo-s9\`.

## Criterio de cierre
- Tests y CI en verde.
- Demo de Renzo: celular con datos móviles abre Tama por HTTPS, la instala como app, conversa y
  recibe un aviso ntfy.
- Tama sigue respondiendo en 127.0.0.1 y ningún puerto del router queda abierto.

## Cierre
Evals solo en la nube y sin Claude; ninguna prueba del modelo local (van en
`13-modelo-local-v2.md`).

- Demo real: un Samsung con datos móviles abrió Tama por HTTPS a través de Tailscale, la instaló
  como app, conversó y recibió los recordatorios por ntfy, también con la pantalla apagada. En el
  PC, Tama siguió respondiendo en 127.0.0.1, sin puertos abiertos en el router.
- Evals: herramientas 23/23, memoria 10/10 y 3/3 sin inventar, conversación 20/20 (media 5,0);
  Sprint 8 sin regresión (planes 7/7, reflexión 3/3).
- Tests: 478 en verde, con una prueba en navegador con pantalla de Pixel 7. CI en verde.

Arreglos durante la demo:
- `tailscale serve` pide activar HTTPS la primera vez: se deja Funnel desmarcado (documentado).
- Tama rechazaba al celular por dos errores de tipeo en el `.env`. El rechazo ahora dice la causa
  y `tama celular` compara el `.env` con lo que reporta Tailscale.
- La página del celular perdía los avisos que llegaban con la pantalla apagada: ahora se pone al
  día al reconectarse, al volver a la pantalla y cada minuto.
- El enlace `ntfy://` dejó la suscripción en ntfy.sh. Estado muestra el servidor y el tópico para
  suscribirse a mano.
- Los recordatorios llegan a ntfy con prioridad alta (suenan y salen bloqueado) y los urgentes al
  máximo.

Pendiente:

- El acceso depende de la cuenta de Tailscale; el login propio llega en el Sprint 10.
- El prompt de la nube sigue en 2.433 tokens (meta ~2.000).
