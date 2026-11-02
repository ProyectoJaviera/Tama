// Avisos en vivo: la campana, una franja fija con los recordatorios sin responder, una notificación
// del navegador y un sonido corto. El diálogo propio reemplaza al confirm() del navegador.
(function () {
  const count = document.getElementById("avisos-n");
  const banner = document.getElementById("banner");
  const box = document.getElementById("toasts");
  const ask = document.getElementById("ask");
  const csrf = document.body.dataset.csrf;
  const sound = new Audio("/static/aviso.wav");

  async function refresh() {
    const response = await fetch("/avisos/pendientes");
    const total = (await response.json()).pendientes;
    count.textContent = total ? "(" + total + ")" : "";
    document.title = total ? "(" + total + ") Tama" : "Tama";
  }

  async function act(id, action) {
    const response = await fetch("/avisos/" + id + "/" + action, {
      method: "POST",
      headers: { "X-CSRF-Token": csrf },
    });
    const panel = document.getElementById("avisos");
    if (panel && response.ok) {
      panel.innerHTML = await response.text();
      htmx.process(panel);
    }
    update();
  }

  function button(label, onClick) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "plain";
    item.textContent = label;
    item.onclick = onClick;
    return item;
  }

  async function showBanner() {
    const open = await (await fetch("/avisos/abiertos")).json();
    banner.replaceChildren();
    banner.hidden = open.length === 0;
    for (const notice of open) {
      const row = document.createElement("div");
      row.className = "banner-row" + (notice.urgent ? " urgent" : "");
      const text = document.createElement("span");
      text.textContent = (notice.urgent ? "Urgente: " : "Recordatorio: ") + notice.text;
      row.append(
        text,
        button("Listo", () => act(notice.id, "listo")),
        button("Posponer 1 h", () => act(notice.id, "posponer")),
      );
      banner.appendChild(row);
    }
  }

  function update() {
    refresh();
    showBanner();
  }

  // Chrome pide el permiso de notificaciones tras un clic: se pregunta una sola vez.
  document.addEventListener(
    "click",
    () => {
      if ("Notification" in window && Notification.permission === "default") {
        Notification.requestPermission();
      }
    },
    { once: true },
  );

  function notify(notice) {
    const reminder = notice.kind === "recordatorio";
    if (reminder) sound.play().catch(() => {});
    if ("Notification" in window && Notification.permission === "granted") {
      new Notification("Tama", {
        body: notice.text,
        tag: "tama-" + notice.id,
        requireInteraction: notice.reason === "urgente",
      });
    }
    const log = document.getElementById("log");
    if (log) {
      const line = document.createElement("div");
      line.className = "notice";
      line.textContent = "Aviso · " + notice.kind + " (" + notice.reason + "): " + notice.text;
      log.appendChild(line);
      log.scrollTop = log.scrollHeight;
      return;
    }
    const toast = document.createElement("a");
    toast.href = "/avisos";
    toast.className = "toast";
    toast.textContent = notice.text;
    box.appendChild(toast);
    setTimeout(() => toast.remove(), 60000);
  }

  const source = new EventSource("/avisos/eventos");
  source.onmessage = (message) => {
    notify(JSON.parse(message.data));
    update();
  };

  document.addEventListener("htmx:confirm", (event) => {
    if (!event.detail.question) return;
    event.preventDefault();
    document.getElementById("ask-text").textContent = event.detail.question;
    document.getElementById("ask-yes").onclick = () => {
      ask.close();
      event.detail.issueRequest(true);
    };
    document.getElementById("ask-no").onclick = () => ask.close();
    ask.showModal();
  });

  update();
})();
