// Voz en la web: un toque en "Hablar" empieza a escuchar con el micrófono del navegador (en el
// celular funciona por el HTTPS de Tailscale). Cuando Renzo hace una pausa, Tama corta sola, manda
// el audio como WAV de 16 kHz para whisper.cpp y lo envía como mensaje. Otro toque envía de
// inmediato. Si "Leer respuestas" está activo, la respuesta se escucha en voz alta.
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

  // Estado explícito: idle → abriendo → escuchando → procesando → idle (siempre vuelve, finally);
  // "hablando" mientras suena la respuesta (un toque la calla y empieza a escuchar).
  // La pausa se detecta por el volumen del audio, en el navegador: no se interpreta lo dicho.
  const LABEL = {
    idle: "Hablar", hablando: "Hablar", abriendo: "Abriendo…", escuchando: "Escuchando…",
    procesando: "Procesando…",
  };
  const PAUSE_MS = 1300;      // silencio tras haber hablado: fin del mensaje
  const WAIT_MS = 8000;       // si en este tiempo no se oye voz, se deja de escuchar
  const MAX_MS = 60000;       // tope de un mensaje
  const CALIBRATE_MS = 250;   // primeros ms: ruido de fondo de la pieza
  const MIN_LEVEL = 0.012;    // volumen mínimo para contar como voz
  const MAX_LEVEL = 0.05;     // el umbral nunca sube de aquí (si la calibración cayó sobre la voz)
  const KEEP_MIC_MS = 120000; // el micrófono queda abierto entre turnos y se cierra tras 2 min sin uso
  let mic = null;
  let micTimer = null;
  let state = "idle";
  let recording = null;
  let playing = null;

  function setState(next, message) {
    state = next;
    talk.dataset.estado = next;
    talk.textContent = LABEL[next];
    talk.classList.toggle("on", next === "abriendo" || next === "escuchando");
    if (message !== undefined) status.textContent = message;
  }

  function stopPlayback() {
    if (playing) { playing.pause(); playing = null; }
    if (state === "hablando") setState("idle");
  }

  async function microphone() {
    clearTimeout(micTimer);
    if (mic && mic.stream.getAudioTracks().some((t) => t.readyState === "live")) {
      if (mic.context.state === "suspended") await mic.context.resume();
      return mic;
    }
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    const context = new AudioContext();
    mic = { stream, context, source: context.createMediaStreamSource(stream) };
    return mic;
  }

  function closeMicLater() {
    clearTimeout(micTimer);
    micTimer = setTimeout(() => {
      if (!mic || state === "abriendo" || state === "escuchando") return;
      mic.stream.getTracks().forEach((t) => t.stop());
      mic.context.close();
      mic = null;
    }, KEEP_MIC_MS);
  }

  function level(samples) {
    let sum = 0;
    for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
    return Math.sqrt(sum / samples.length);
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
    try {
      const { context, source } = await microphone();
      const node = context.createScriptProcessor(4096, 1, 1);
      const chunks = [];
      const started = performance.now();
      let floor = 0;
      let calibrated = 0;
      let voiceAt = 0;
      node.onaudioprocess = (e) => {
        if (!recording || state !== "escuchando") return;
        const samples = new Float32Array(e.inputBuffer.getChannelData(0));
        chunks.push(samples);
        const now = performance.now();
        const rms = level(samples);
        if (now - started < CALIBRATE_MS) {
          floor = (floor * calibrated + rms) / (calibrated + 1);
          calibrated += 1;
          return;
        }
        if (rms > Math.max(MIN_LEVEL, Math.min(floor * 3, MAX_LEVEL))) voiceAt = now;
        if (voiceAt && now - voiceAt > PAUSE_MS) finish();
        else if (!voiceAt && now - started > WAIT_MS) cancel("No te escuché. Toca Hablar y vuelve a intentarlo.");
        else if (now - started > MAX_MS) finish();
      };
      source.connect(node);
      node.connect(context.destination);
      recording = { source, node, chunks, rate: context.sampleRate };
      setState("escuchando", "Te escucho… cuando hagas una pausa, lo envío.");
    } catch (error) {
      setState("idle", "No pude usar el micrófono: " + error.message);
    }
  }

  function release() {
    const taken = recording;
    recording = null;
    if (!taken) return null;
    taken.node.onaudioprocess = null;
    try { taken.source.disconnect(taken.node); } catch (e) {}
    taken.node.disconnect();
    closeMicLater();
    return { chunks: taken.chunks, rate: taken.rate };
  }

  function cancel(message) {
    release();
    setState("idle", message);
  }

  async function finish() {
    if (state !== "escuchando") return;
    const taken = release();
    if (!taken) { setState("idle"); return; }
    setState("procesando", "Transcribiendo…");
    try {
      const response = await fetch("/voz/escuchar", {
        method: "POST",
        headers: { "Content-Type": "audio/wav", "X-CSRF-Token": csrf },
        body: wav16k(taken.chunks, taken.rate),
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

  // Un toque empieza a escuchar; otro toque mientras escucha envía de inmediato.
  talk.addEventListener("click", (event) => {
    event.preventDefault();
    if (state === "escuchando") { finish(); return; }
    if (state !== "idle" && state !== "hablando") return;
    stopPlayback(); // tocar Hablar corta la respuesta que está sonando
    open();
  });

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
      if (state !== "idle" && state !== "hablando") return; // Renzo ya habla: no se le encima la voz
      stopPlayback();
      const audio = new Audio(URL.createObjectURL(blob));
      playing = audio;
      audio.onended = () => { if (playing === audio) { playing = null; setState("idle"); } };
      status.textContent = "Voz: " + (response.headers.get("X-Tama-Voz") || "");
      setState("hablando");
      await audio.play();
    } catch (error) {
      if (state === "hablando") setState("idle");
      if (error.name !== "AbortError") status.textContent = error.message;
    }
  });
})();
