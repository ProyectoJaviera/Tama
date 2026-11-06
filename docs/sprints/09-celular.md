# Sprint 9: Tama en el celular

Estado: en curso

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
(se completan al cierre)

## Criterio de cierre
- Tests y CI en verde.
- Demo de Renzo: celular con datos móviles abre Tama por HTTPS, la instala como app, conversa y
  recibe un aviso ntfy.
- Tama sigue respondiendo en 127.0.0.1 y ningún puerto del router queda abierto.
