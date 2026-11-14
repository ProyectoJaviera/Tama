// Voz en la web: mantener apretado "Hablar" (o tocarlo una vez para empezar y otra para terminar) graba con el micrófono del navegador (en el celular
// funciona por el HTTPS de Tailscale), lo manda a Tama como WAV de 16 kHz para whisper.cpp y lo
// envía como mensaje. Si "Leer respuestas" está activo, la respuesta se escucha en voz alta.
(() => {
  const talk = document.getElementById("hablar");
  const options = document.getElementById("voz-opciones");
  const readAloud = document.getElementById("voz-leer");
  const status = document.getElementById("voz-estado");
  const input = document.getElementById("chat-text");
  const form = document.getElementById("chat-form");
  const csrf = document.body.dataset.csrf;
  if (!talk || !navigator.mediaDevices || !window.AudioContext) return;
  talk.hidden = false;
  options.hidden = false;

  try { readAloud.checked = localStorage.getItem("tama-voz-leer") === "1"; } catch (e) {}
  readAloud.onchange = () => {
    try { localStorage.setItem("tama-voz-leer", readAloud.checked ? "1" : "0"); } catch (e) {}
  };

  // Estado explícito: idle → abriendo (pidiendo el micrófono) → grabando → procesando → idle.
  // Siempre se vuelve a idle (finally). Soltar mientras el micrófono abre no pierde la grabación:
  // pasa al modo "toca para terminar" (antes quedaba grabando para siempre y el botón no respondía).
  const LABEL = { idle: "Hablar", abriendo: "Abriendo…", grabando: "Grabando…", procesando: "Procesando…" };
  const QUICK_MS = 350; // un toque más corto que esto es "toca para empezar, toca para terminar"
  let state = "idle";
  let mode = "hold";
  let pressed = false;
  let pressedAt = 0;
  let recording = null;
  let playing = null;

  function setState(next, message) {
    state = next;
    talk.dataset.estado = next;
    talk.textContent = LABEL[next];
    talk.classList.toggle("on", next === "abriendo" || next === "grabando");
    if (message !== undefined) status.textContent = message;
  }

  function stopPlayback() {
    if (playing) { playing.pause(); playing = null; }
  }

  function wav16k(chunks, rate) {
    let length = chunks.reduce((sum, c) => sum + c.length, 0);
    const all = new Float32Array(length);
    let offset = 0;
    for (const c of chunks) { all.set(c, offset); offset += c.length; }
    const ratio = rate / 16000;
    length = Math.floor(all.length / ratio);
    const view = new DataView(new ArrayBuffer(44 + length * 2));
    const text = (at, s) => { for (let i = 0; i < s.length; i++) view.setUint8(at + i, s.charCodeAt(i)); };
    text(0, "RIFF"); view.setUint32(4, 36 + length * 2, true); text(8, "WAVE"); text(12, "fmt ");
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, 16000, true); view.setUint32(28, 32000, true); view.setUint16(32, 2, true);
    view.setUint16(34, 16, true); text(36, "data"); view.setUint32(40, length * 2, true);
    for (let i = 0; i < length; i++) {
      const sample = Math.max(-1, Math.min(1, all[Math.floor(i * ratio)]));
      view.setInt16(44 + i * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    }
    return new Blob([view], { type: "audio/wav" });
  }

  async function open() {
    setState("abriendo", "Abriendo el micrófono…");
    let stream = null;
    let context = null;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      context = new AudioContext();
      const source = context.createMediaStreamSource(stream);
      const node = context.createScriptProcessor(4096, 1, 1);
      const chunks = [];
      node.onaudioprocess = (e) => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
      source.connect(node);
      node.connect(context.destination);
      recording = { stream, context, node, chunks };
    } catch (error) {
      if (stream) stream.getTracks().forEach((t) => t.stop());
      if (context) context.close();
      setState("idle", "No pude usar el micrófono: " + error.message);
      return;
    }
    if (pressed) {
      mode = "hold";
      setState("grabando", "Grabando… suelta para enviar.");
    } else {
      mode = "toggle"; // ya soltó: sigue grabando hasta el próximo toque
      setState("grabando", "Grabando… toca Hablar para enviar.");
    }
  }

  async function finish() {
    if (state !== "grabando" || !recording) return;
    const { stream, context, node, chunks } = recording;
    recording = null;
    setState("procesando", "Transcribiendo…");
    try {
      node.disconnect();
      stream.getTracks().forEach((t) => t.stop());
      const audio = wav16k(chunks, context.sampleRate);
      context.close();
      const response = await fetch("/voz/escuchar", {
        method: "POST",
        headers: { "Content-Type": "audio/wav", "X-CSRF-Token": csrf },
        body: audio,
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "No pude transcribir.");
      if (!data.text) { status.textContent = "No te escuché. Vuelve a intentarlo."; return; }
      status.textContent = "";
      input.value = data.text;
      form.requestSubmit();
    } catch (error) {
      status.textContent = error.message;
    } finally {
      setState("idle");
    }
  }

  talk.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    if (event.button !== undefined && event.button !== 0) return;
    if (state === "grabando" && mode === "toggle") { finish(); return; }
    if (state !== "idle") return;
    stopPlayback(); // apretar Hablar corta la respuesta que está sonando
    pressed = true;
    pressedAt = performance.now();
    try { talk.setPointerCapture(event.pointerId); } catch (e) {}
    open();
  });

  function release(event) {
    event.preventDefault();
    if (!pressed) return;
    pressed = false;
    if (state !== "grabando" || mode !== "hold") return; // si aún abre, open() pasa a toggle
    if (performance.now() - pressedAt < QUICK_MS) {
      mode = "toggle";
      status.textContent = "Grabando… toca Hablar para enviar.";
      return;
    }
    finish();
  }

  talk.addEventListener("pointerup", release);
  talk.addEventListener("pointercancel", release);
  talk.addEventListener("contextmenu", (e) => e.preventDefault());

  document.addEventListener("tama:respuesta", async (event) => {
    if (!readAloud.checked || !event.detail) return;
    try {
      const body = new URLSearchParams({ texto: event.detail });
      const response = await fetch("/voz/hablar", {
        method: "POST",
        headers: { "X-CSRF-Token": csrf },
        body,
      });
      if (!response.ok) throw new Error((await response.json()).error || "No pude hablar.");
      const blob = await response.blob();
      if (state !== "idle") return; // Renzo ya está hablando de nuevo: no se le encima la voz
      stopPlayback();
      playing = new Audio(URL.createObjectURL(blob));
      status.textContent = "Voz: " + (response.headers.get("X-Tama-Voz") || "");
      await playing.play();
    } catch (error) {
      if (error.name !== "AbortError") status.textContent = error.message;
    }
  });
})();
