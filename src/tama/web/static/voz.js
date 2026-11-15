// Voz en la web, modo conversación: un toque en "Hablar" empieza a conversar con el micrófono del
// navegador (en el celular funciona por el HTTPS de Tailscale). Ciclo: escuchar → pausa → enviar
// (WAV de 16 kHz) → Tama responde y lo dice en voz alta → al terminar de hablar, vuelve a escuchar
// sola. No escucha mientras Tama habla. Termina con un toque, cuando el modelo llama
// `terminar_conversacion` (Renzo se despidió), tras dos escuchas seguidas sin voz o si la página
// pasa a segundo plano. En el celular, la pantalla no se apaga mientras dura (Wake Lock).
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
  // Los tiempos se pueden ajustar con window.TAMA_VOZ (los tests los acortan).
  const CFG = Object.assign({ pauseMs: 1300, waitMs: 8000, misses: 2 }, window.TAMA_VOZ || {});
  const PAUSE_MS = CFG.pauseMs; // silencio tras haber hablado: fin del mensaje
  const WAIT_MS = CFG.waitMs;   // si en este tiempo no se oye voz, esa escucha no cuenta
  const MAX_MS = 60000;       // tope de un mensaje
  const CALIBRATE_MS = 250;   // sin audio previo: los primeros ms dan el ruido de fondo (se guardan)
  const PREROLL_MS = 500;     // con el micrófono abierto, el último medio segundo antes del toque
  const MIN_LEVEL = 0.012;    // volumen mínimo para contar como voz
  const MAX_LEVEL = 0.05;     // el umbral nunca sube de aquí (si la calibración cayó sobre la voz)
  const KEEP_MIC_MS = 120000; // el micrófono queda abierto entre turnos y se cierra tras 2 min sin uso
  let mic = null;
  let micTimer = null;
  let state = "idle";
  let recording = null;
  let playing = null;
  let conv = null; // { misses, awaiting, ending, wake } mientras dura la conversación

  function setState(next, message) {
    state = next;
    talk.dataset.estado = next;
    talk.textContent = conv ? "Conversando… (toca para terminar)" : LABEL[next];
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
      // Sin supresión de ruido: deforma la voz y a whisper le va peor; el eco sí se cancela.
      audio: { echoCancellation: true, noiseSuppression: false, autoGainControl: true },
    });
    const context = new AudioContext();
    const source = context.createMediaStreamSource(stream);
    // Un solo procesador mientras el micrófono esté abierto: entre turnos guarda el último medio
    // segundo (pre-roll), para no cortar la primera sílaba si Renzo habla justo al tocar.
    const node = context.createScriptProcessor(2048, 1, 1);
    mic = { stream, context, source, node, ring: [], ringLength: 0 };
    const keep = (PREROLL_MS / 1000) * context.sampleRate;
    node.onaudioprocess = (e) => {
      const samples = new Float32Array(e.inputBuffer.getChannelData(0));
      if (recording) { recording.feed(samples); return; }
      mic.ring.push(samples);
      mic.ringLength += samples.length;
      while (mic.ring.length > 1 && mic.ringLength - mic.ring[0].length >= keep) {
        mic.ringLength -= mic.ring.shift().length;
      }
    };
    source.connect(node);
    node.connect(context.destination);
    return mic;
  }

  function closeMicLater() {
    clearTimeout(micTimer);
    micTimer = setTimeout(() => {
      if (!mic || state === "abriendo" || state === "escuchando") return;
      mic.node.onaudioprocess = null;
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

  // A 16 kHz con el remuestreador del navegador (filtra antes de bajar la frecuencia). Tomar 1 de
  // cada 3 muestras de 48 kHz, como antes, metía ruido de aliasing que empeoraba a whisper.
  async function wav16k(chunks, rate) {
    const length = chunks.reduce((sum, c) => sum + c.length, 0);
    const all = new Float32Array(length);
    let offset = 0;
    for (const c of chunks) { all.set(c, offset); offset += c.length; }
    let samples = all;
    if (rate !== 16000 && length > 0) {
      const offline = new OfflineAudioContext(1, Math.ceil((length * 16000) / rate), 16000);
      const buffer = offline.createBuffer(1, length, rate);
      buffer.copyToChannel(all, 0);
      const source = offline.createBufferSource();
      source.buffer = buffer;
      source.connect(offline.destination);
      source.start();
      samples = (await offline.startRendering()).getChannelData(0);
    }
    const view = new DataView(new ArrayBuffer(44 + samples.length * 2));
    const text = (at, s) => { for (let i = 0; i < s.length; i++) view.setUint8(at + i, s.charCodeAt(i)); };
    text(0, "RIFF"); view.setUint32(4, 36 + samples.length * 2, true); text(8, "WAVE"); text(12, "fmt ");
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, 16000, true); view.setUint32(28, 32000, true); view.setUint16(32, 2, true);
    view.setUint16(34, 16, true); text(36, "data"); view.setUint32(40, samples.length * 2, true);
    for (let i = 0; i < samples.length; i++) {
      const sample = Math.max(-1, Math.min(1, samples[i]));
      view.setInt16(44 + i * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    }
    return new Blob([view], { type: "audio/wav" });
  }

  // `withPreroll`: al tocar Hablar se incluye el medio segundo anterior; al volver a escuchar
  // después de que Tama habló, no (traería el final de su propia voz).
  async function open(withPreroll = true) {
    setState("abriendo", "Abriendo el micrófono…");
    try {
      const current = await microphone();
      if (!conv) return setState("idle"); // la conversación terminó mientras abría
      const kept = current.ring.splice(0);
      const preroll = withPreroll ? kept : [];
      current.ringLength = 0;
      const chunks = [...preroll];
      const started = performance.now();
      // El ruido de fondo se mide con el audio de antes del toque; si no hay (micrófono recién
      // abierto), con los primeros 250 ms, que igual se guardan y se envían.
      const levels = preroll.map(level).sort((a, b) => a - b);
      let floor = levels.length ? levels[Math.floor(levels.length / 2)] : 0;
      let calibrating = levels.length === 0;
      let calibrated = 0;
      let voiceAt = 0;
      recording = {
        chunks,
        rate: current.context.sampleRate,
        feed(samples) {
          if (state !== "escuchando") return;
          chunks.push(samples);
          const now = performance.now();
          const rms = level(samples);
          if (calibrating && now - started < CALIBRATE_MS) {
            floor = (floor * calibrated + rms) / (calibrated + 1);
            calibrated += 1;
            return;
          }
          calibrating = false;
          if (rms > Math.max(MIN_LEVEL, Math.min(floor * 3, MAX_LEVEL))) voiceAt = now;
          if (voiceAt && now - voiceAt > PAUSE_MS) finish();
          else if (!voiceAt && now - started > WAIT_MS) missed();
          else if (now - started > MAX_MS) finish();
        },
      };
      setState("escuchando", "Te escucho… cuando hagas una pausa, lo envío.");
      beep([880]);
    } catch (error) {
      recording = null;
      setState("idle", "No pude usar el micrófono: " + error.message);
    }
  }

  function release() {
    const taken = recording;
    recording = null;
    if (!taken) return null;
    closeMicLater();
    return { chunks: taken.chunks, rate: taken.rate };
  }

  // Un tono corto: al empezar a escuchar (agudo) y al terminar la conversación (dos, bajando).
  function beep(freqs) {
    try {
      const ctx = mic ? mic.context : new AudioContext();
      let at = ctx.currentTime;
      for (const freq of freqs) {
        const tone = ctx.createOscillator();
        const gain = ctx.createGain();
        tone.frequency.value = freq;
        gain.gain.setValueAtTime(0.06, at);
        gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.12);
        tone.connect(gain).connect(ctx.destination);
        tone.start(at);
        tone.stop(at + 0.13);
        at += 0.16;
      }
    } catch (e) {}
  }

  async function startConversation() {
    conv = { misses: 0, awaiting: false, ending: false, wake: null };
    document.body.dataset.conversando = "1";
    stopPlayback();
    try { conv.wake = await navigator.wakeLock.request("screen"); } catch (e) {}
    if (conv) open(true);
  }

  function endConversation(message) {
    if (!conv) return;
    const { wake } = conv;
    conv = null;
    document.body.dataset.conversando = "";
    release();
    stopPlayback();
    try { if (wake) wake.release(); } catch (e) {}
    beep([660, 440]);
    setState("idle", message);
  }

  // Volver a escuchar sola (después de que Tama habló o de una escucha vacía).
  function listenAgain() {
    if (!conv) return;
    setState("idle", "Te escucho de nuevo…");
    setTimeout(() => { if (conv && state === "idle") open(false); }, 250);
  }

  function missed() {
    release();
    if (!conv) return setState("idle");
    conv.misses += 1;
    if (conv.misses >= CFG.misses) {
      endConversation("Terminé la conversación: no te escuché. Toca Hablar para seguir.");
    } else {
      listenAgain();
    }
  }

  async function finish() {
    if (state !== "escuchando") return;
    const taken = release();
    if (!taken) { setState("idle"); return; }
    setState("procesando", "Transcribiendo…");
    let sent = false;
    try {
      const response = await fetch("/voz/escuchar", {
        method: "POST",
        headers: { "Content-Type": "audio/wav", "X-CSRF-Token": csrf },
        body: await wav16k(taken.chunks, taken.rate),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "No pude transcribir.");
      if (data.text && conv) {
        conv.misses = 0;
        conv.awaiting = true;
        status.textContent = "";
        input.value = data.text;
        form.requestSubmit();
        sent = true;
      }
    } catch (error) {
      status.textContent = error.message;
    } finally {
      if (!sent && conv) { setState("procesando"); missed(); } else setState(conv ? "procesando" : "idle");
    }
  }

  // Un toque empieza la conversación; otro toque, en cualquier momento, la termina.
  talk.addEventListener("click", (event) => {
    event.preventDefault();
    if (conv) { endConversation("Terminaste la conversación."); return; }
    startConversation();
  });

  // El modelo llamó terminar_conversacion: se cierra después de que Tama se despida.
  document.addEventListener("tama:fin-conversacion", () => { if (conv) conv.ending = true; });

  // El turno terminó sin respuesta para leer (un error, un comando): seguir escuchando.
  document.addEventListener("tama:turno-fin", () => {
    if (!conv || !conv.awaiting) return;
    conv.awaiting = false;
    afterSpeech();
  });

  function afterSpeech() {
    if (!conv) return;
    if (conv.ending) endConversation("Conversación terminada. Toca Hablar para empezar otra.");
    else listenAgain();
  }

  // Android suspende la página al bloquear o cambiar de app: se sale limpio del modo.
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && conv) endConversation("Conversación terminada: la app quedó en segundo plano.");
  });

  document.addEventListener("tama:respuesta", async (event) => {
    const talking = conv !== null;
    if (talking) conv.awaiting = false;
    if ((!readAloud.checked && !talking) || !event.detail) {
      if (talking) afterSpeech();
      return;
    }
    try {
      const body = new URLSearchParams({ texto: event.detail });
      const response = await fetch("/voz/hablar", {
        method: "POST",
        headers: { "X-CSRF-Token": csrf },
        body,
      });
      if (!response.ok) throw new Error((await response.json()).error || "No pude hablar.");
      const blob = await response.blob();
      if (talking && !conv) return; // la conversación terminó mientras se preparaba la voz
      if (!talking && state !== "idle" && state !== "hablando") return;
      stopPlayback();
      const audio = new Audio(URL.createObjectURL(blob));
      playing = audio;
      const done = () => {
        if (playing !== audio) return;
        playing = null;
        setState("idle");
        afterSpeech();
      };
      audio.onended = done;
      audio.onerror = done;
      status.textContent = "Voz: " + (response.headers.get("X-Tama-Voz") || "");
      setState("hablando"); // mientras Tama habla no se escucha: no se transcribe a sí misma
      await audio.play();
    } catch (error) {
      if (state === "hablando") setState("idle");
      if (error.name !== "AbortError") status.textContent = error.message;
      afterSpeech();
    }
  });
})();
