// Service worker de Tama: guarda solo la interfaz (estilos, scripts, íconos y la página sin
// conexión). Nunca guarda páginas ni respuestas con datos: chat, memoria, objetivos, avisos y
// todo lo que no sea GET van siempre a la red. La versión cambia con cada cambio de static/.
const CACHE = "tama-interfaz-__VERSION__";
const SHELL = [
  "/static/style.css?v=__VERSION__",
  "/static/htmx.min.js?v=__VERSION__",
  "/static/app.js?v=__VERSION__",
  "/static/avisos.js?v=__VERSION__",
  "/static/pwa.js?v=__VERSION__",
  "/static/offline.html",
  "/static/icono-192.png",
  "/static/icono-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((n) => n !== CACHE).map((n) => caches.delete(n)))
    )
  );
  self.clients.claim();
});

// Solo la interfaz: /static/ (menos el sonido, que no hace falta guardar).
function isShell(url) {
  return url.origin === self.location.origin && url.pathname.startsWith("/static/") &&
    !url.pathname.endsWith(".wav");
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (isShell(url)) {
    event.respondWith(
      caches.match(request).then((found) => found || fetch(request))
    );
    return;
  }
  // Las páginas siempre van a la red; sin red, se muestra la página sin conexión (sin datos).
  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(() => caches.match("/static/offline.html")));
  }
});
