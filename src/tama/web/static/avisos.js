// Avisos en vivo: la campana y un mensaje breve en cualquier pestaña.
(function () {
  const count = document.getElementById("avisos-n");
  const box = document.getElementById("toasts");

  async function refresh() {
    const response = await fetch("/avisos/pendientes");
    const total = (await response.json()).pendientes;
    count.textContent = total ? "(" + total + ")" : "";
  }

  const source = new EventSource("/avisos/eventos");
  source.onmessage = (message) => {
    const notice = JSON.parse(message.data);
    const toast = document.createElement("a");
    toast.href = "/avisos";
    toast.className = "toast";
    toast.textContent = notice.text;
    box.appendChild(toast);
    setTimeout(() => toast.remove(), 60000);
    refresh();
  };
  refresh();
})();
