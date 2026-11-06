// Registra el service worker (solo guarda la interfaz; ver /sw.js). Sin HTTPS ni localhost el
// navegador no lo permite y Tama funciona igual, sin instalarse.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}
