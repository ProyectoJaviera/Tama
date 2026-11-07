// Voz en la web: mantener apretado "Hablar" graba con el micrófono del navegador (en el celular
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

  let recording = null;

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

  async function start(event) {
    event.preventDefault();
    if (recording) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const context = new AudioContext();
      const source = context.createMediaStreamSource(stream);
      const node = context.createScriptProcessor(4096, 1, 1);
      const chunks = [];
      node.onaudioprocess = (e) => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
      source.connect(node);
      node.connect(context.destination);
      recording = { stream, context, node, chunks };
      talk.classList.add("on");
      status.textContent = "Escuchando… suelta para enviar.";
    } catch (error) {
      status.textContent = "No pude usar el micrófono: " + error.message;
    }
  }

  async function stop(event) {
    event.preventDefault();
    if (!recording) return;
    const { stream, context, node, chunks } = recording;
    recording = null;
    talk.classList.remove("on");
    node.disconnect();
    stream.getTracks().forEach((t) => t.stop());
    const audio = wav16k(chunks, context.sampleRate);
    context.close();
    status.textContent = "Transcribiendo…";
    try {
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
    }
  }

  talk.addEventListener("pointerdown", start);
  talk.addEventListener("pointerup", stop);
  talk.addEventListener("pointerleave", stop);
  talk.addEventListener("contextmenu", (e) => e.preventDefault());

  let playing = null;
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
      if (playing) playing.pause();
      playing = new Audio(URL.createObjectURL(await response.blob()));
      status.textContent = "Voz: " + (response.headers.get("X-Tama-Voz") || "");
      await playing.play();
    } catch (error) {
      status.textContent = error.message;
    }
  });
})();
