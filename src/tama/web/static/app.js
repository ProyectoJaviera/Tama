// Chat de Tama: envía el mensaje, recibe el turno por SSE y muestra las confirmaciones.
(function () {
  const form = document.getElementById("chat-form");
  if (!form) return;
  const log = document.getElementById("log");
  const input = document.getElementById("chat-text");
  const button = form.querySelector("button");
  const dialog = document.getElementById("confirm");
  const question = document.getElementById("confirm-text");
  const csrf = document.body.dataset.csrf;
  let turnId = null;

  function add(kind, text) {
    const item = document.createElement("div");
    item.className = kind;
    item.textContent = text;
    log.appendChild(item);
    log.scrollTop = log.scrollHeight;
    return item;
  }

  function post(url, data) {
    return fetch(url, {
      method: "POST",
      headers: { "X-CSRF-Token": csrf },
      body: new URLSearchParams(data),
    });
  }

  let pendingCommand = false;

  async function answer(value) {
    dialog.close();
    if (pendingCommand) {
      pendingCommand = false;
      const response = await post("/chat/comando/confirmar", { respuesta: value });
      add("command", (await response.json()).reply);
      button.disabled = false;
      input.focus();
      return;
    }
    post("/chat/" + turnId + "/confirmar", { respuesta: value });
  }
  document.getElementById("confirm-yes").onclick = () => answer("si");
  document.getElementById("confirm-no").onclick = () => answer("no");
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    answer("no");
  });

  // Algunos modelos marcan **negritas**: se muestran como negrita, sin usar innerHTML.
  function emphasize(item) {
    const parts = item.textContent.split(/\*\*(.+?)\*\*/s);
    if (parts.length < 2) return;
    item.replaceChildren(
      ...parts.map((part, i) => {
        if (i % 2 === 0) return document.createTextNode(part);
        const strong = document.createElement("strong");
        strong.textContent = part;
        return strong;
      }),
    );
  }

  function addTool(event) {
    const box = document.createElement("details");
    box.className = "tool";
    const title = document.createElement("summary");
    title.textContent = (event.server || "?") + "." + event.name;
    const body = document.createElement("pre");
    body.textContent = JSON.stringify(event.args, null, 1);
    box.append(title, body);
    log.appendChild(box);
    return body;
  }

  function follow() {
    const source = new EventSource("/chat/" + turnId + "/eventos");
    let reply = null;
    let tool = null;
    source.onmessage = (message) => {
      const event = JSON.parse(message.data);
      if (event.kind === "text") {
        reply = reply || add("assistant", "");
        reply.textContent += event.text;
        log.scrollTop = log.scrollHeight;
      } else if (event.kind === "tool") {
        tool = addTool(event);
        reply = null;
      } else if (event.kind === "tool_result") {
        if (tool) tool.textContent += "\n-> " + event.text;
        if (event.skipped) add("line", "no se ejecutó (sin confirmación)");
      } else if (event.kind === "confirm") {
        question.textContent = event.question;
        dialog.showModal();
      } else if (event.kind === "confirm_closed") {
        if (dialog.open) dialog.close();
        add("line", "se agotó el tiempo: no se ejecutó");
      } else if (event.kind === "note") {
        reply = null;
        add("line", event.text);
      } else if (event.kind === "done") {
        add("line", event.line);
      } else if (event.kind === "error") {
        add("error", event.text);
      }
    };
    source.addEventListener("end", () => {
      log.querySelectorAll(".assistant").forEach(emphasize);
      source.close();
      button.disabled = false;
      input.focus();
    });
  }

  // Varias opciones (por ejemplo, qué recordatorio cancelar): un botón por cada una. También
  // sirven los que vuelven del historial al recargar.
  function addChoices(choices) {
    const row = document.createElement("div");
    row.className = "row choices";
    for (const choice of choices) {
      const option = document.createElement("button");
      option.type = "button";
      option.className = "plain";
      option.dataset.command = choice.command;
      option.textContent = choice.label;
      row.appendChild(option);
    }
    log.appendChild(row);
    log.scrollTop = log.scrollHeight;
  }

  log.addEventListener("click", (event) => {
    const option = event.target.closest(".choices button");
    if (!option || button.disabled) return;
    option.parentElement.remove();
    input.value = option.dataset.command;
    form.requestSubmit();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text || button.disabled) return;
    button.disabled = true;
    input.value = "";
    add("user", text);
    const response = await post("/chat", { text: text });
    if (!response.ok) {
      add("error", "Tama aún está respondiendo.");
      button.disabled = false;
      return;
    }
    const result = await response.json();
    if (result.reply !== undefined) {
      add("command", result.reply);
      if (result.choices) addChoices(result.choices);
      if (result.confirm) {
        pendingCommand = true;
        question.textContent = result.confirm;
        dialog.showModal();
        return;
      }
      button.disabled = false;
      input.focus();
      return;
    }
    turnId = result.turn;
    follow();
  });
  log.querySelectorAll(".assistant").forEach(emphasize);
  log.scrollTop = log.scrollHeight;
})();
