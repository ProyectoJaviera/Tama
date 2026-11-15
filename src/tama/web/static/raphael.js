// Raphael: la figura de Tama en el modo conversación por voz (prototipo aprobado por Renzo,
// prototipos/raphael/raphael-v2.html). Se dibuja con código en un canvas, sin red ni imágenes.
// voz.js la controla: show() al empezar a conversar, state(...) con el estado real, attach(audio)
// para que siga la voz de Tama, input(nivel) con el micrófono, hide() al terminar.
(() => {
  "use strict";
  const view = document.getElementById("raphael");
  if (!view) return;
  const canvas = view.querySelector("canvas");
  const ctx = canvas.getContext("2d");
  const lineRenzo = view.querySelector(".raphael-renzo");
  const lineTama = view.querySelector(".raphael-tama");
  const REDUCED = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Proporciones medidas en la referencia (radio exterior de la banda = 1).
  const P = {
    bandOuter: 1.0, bandInner: 0.76,       // banda de escritura
    ringR: 0.62, ringMin: 0.075, ringMax: 0.135, // anillo blanco: radio medio y grosor
    interior: 0.77,                         // disco interior oscuro y translúcido
    rayMax: 0.54,                           // los rayos llegan casi al anillo
    gapAt: -0.95, gapSize: 0.10,            // corte en la escritura (arriba a la derecha)
  };
  const COLORS = {
    glyphCore: "#ffd24a", glyph: "#ff9a12", glyphEdge: "#5c1f00", bandLine: "#ff9a1f",
    ring: "#ffffff", ringWarm: "255,236,170", ray: "255,240,150", core: "255,252,235",
    coreWarm: "255,214,70", interior: "rgba(52,46,14,0.62)", halo: "255,168,40",
  };

  // Aleatorio determinista: los glifos (escritura inventada) son siempre los mismos.
  function rng(seed) { return () => { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; }; }
  const rand = rng(11);

  // Glifos: trazos sobre una grilla alta (3 x 5), con curvas, como una escritura densa.
  const GLYPH_COUNT = 62;
  const GLYPHS = Array.from({ length: GLYPH_COUNT }, () => {
    const strokes = [];
    const ys = [0, 0.33, 0.66, 1];
    const stems = rand() < 0.55 ? [0, 1] : [rand() < 0.5 ? 0 : 1];
    for (const x of stems) {
      const y0 = rand() < 0.75 ? 0 : 0.33, y1 = rand() < 0.75 ? 1 : 0.66;
      strokes.push({ t: "l", a: [x, y0], b: [x, y1] });
    }
    const bars = 1 + Math.floor(rand() * 2);
    for (let i = 0; i < bars; i++) {
      const y = ys[Math.floor(rand() * ys.length)];
      strokes.push({ t: "l", a: [0, y], b: [rand() < 0.7 ? 1 : 0.5, y] });
    }
    const r = rand();
    if (r < 0.35) strokes.push({ t: "q", a: [0, 0.5], c: [1.25, 0.25], b: [0, 0] });          // panza
    else if (r < 0.55) strokes.push({ t: "o", a: [0.5, rand() < 0.5 ? 0.3 : 0.7], r: 0.32 }); // lazo
    else if (r < 0.75) strokes.push({ t: "l", a: [0, 1], b: [1, 0] });                        // diagonal
    return strokes;
  });

  // Anillo: 14 lados con vértices y lados apenas irregulares, como dibujado a mano.
  const SIDES = 14;
  const VERT = Array.from({ length: SIDES }, () => ({ r: 1 + (rand() - 0.5) * 0.04, a: (rand() - 0.5) * 0.05, bulge: (rand() - 0.5) * 0.03 }));

  // ---------------------------------------------------------------------------------------
  // Lienzos: escena nítida + dos niveles de resplandor (a 1/4 y 1/8 de resolución).
  const scene = document.createElement("canvas"), sctx = scene.getContext("2d");
  const bloomA = document.createElement("canvas"), actx = bloomA.getContext("2d");
  const bloomB = document.createElement("canvas"), bctx = bloomB.getContext("2d");
  const tmp = document.createElement("canvas"), tctx = tmp.getContext("2d");
  const FILTER_OK = (() => { const t = document.createElement("canvas").getContext("2d"); t.filter = "blur(2px)"; return t.filter === "blur(2px)"; })();

  let W = 0, H = 0, R = 0, dpr = 1, maxDpr = 2, quality = 2, bandSprite = null;

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, maxDpr);
    const box = canvas.getBoundingClientRect();
    W = Math.max(2, Math.round(box.width * dpr)); H = Math.max(2, Math.round(box.height * dpr));
    for (const c of [canvas, scene]) { c.width = W; c.height = H; }
    bloomA.width = tmp.width = Math.ceil(W / 4); bloomA.height = tmp.height = Math.ceil(H / 4);
    bloomB.width = Math.ceil(W / 8); bloomB.height = Math.ceil(H / 8);
    R = Math.min(W, H) * 0.36;
    bandSprite = renderBand();
  }

  // La escritura se dibuja una vez en un sprite (lo caro) y luego se escala, gira e ilumina.
  function renderBand() {
    const size = Math.ceil(R * 2.12);
    const c = document.createElement("canvas"); c.width = c.height = size;
    const g = c.getContext("2d");
    const cx = size / 2, inner = R * P.bandInner, outer = R * P.bandOuter;
    // Fondo apenas tostado de la banda.
    g.beginPath(); g.arc(cx, cx, outer, 0, Math.PI * 2); g.arc(cx, cx, inner, 0, Math.PI * 2, true);
    g.fillStyle = "rgba(150,62,0,0.38)"; g.fill("evenodd");
    // Líneas finas de borde.
    g.strokeStyle = COLORS.bandLine; g.lineWidth = Math.max(1, R * 0.006);
    for (const r of [outer - R * 0.004, inner + R * 0.004]) { g.beginPath(); g.arc(cx, cx, r, 0, Math.PI * 2); g.stroke(); }
    // Glifos: contorno oscuro, cuerpo naranja y alma clara (el peso visual de la referencia).
    const mid = (inner + outer) / 2, h = (outer - inner) * 0.7, step = (Math.PI * 2) / GLYPH_COUNT;
    const w = Math.min(h * 0.62, mid * step * 0.7);
    const passes = [
      { color: COLORS.glyphEdge, width: R * 0.04 },
      { color: COLORS.glyph, width: R * 0.029 },
      { color: COLORS.glyphCore, width: R * 0.009 },
    ];
    g.lineCap = "round"; g.lineJoin = "round";
    for (const pass of passes) {
      g.strokeStyle = pass.color; g.lineWidth = Math.max(1, pass.width);
      GLYPHS.forEach((strokes, i) => {
        const a = i * step + step / 2;
        let d = Math.atan2(Math.sin(a - P.gapAt), Math.cos(a - P.gapAt));
        if (Math.abs(d) < P.gapSize / 2) return; // corte en la escritura
        g.save();
        g.translate(cx + Math.cos(a) * mid, cx + Math.sin(a) * mid);
        g.rotate(a + Math.PI / 2);
        g.translate(-w / 2, -h / 2);
        for (const s of strokes) {
          g.beginPath();
          if (s.t === "l") { g.moveTo(s.a[0] * w, s.a[1] * h); g.lineTo(s.b[0] * w, s.b[1] * h); }
          else if (s.t === "q") { g.moveTo(s.a[0] * w, s.a[1] * h); g.quadraticCurveTo(s.c[0] * w, s.c[1] * h, s.b[0] * w, s.b[1] * h); }
          else { g.ellipse(s.a[0] * w, s.a[1] * h, s.r * w, s.r * h * 0.7, 0, 0, Math.PI * 2); }
          g.stroke();
        }
        g.restore();
      });
    }
    return c;
  }

  // ---------------------------------------------------------------------------------------

  // Estados con transición suave: cada parámetro se acerca a su objetivo en cada cuadro.
  const TARGETS = {
    reposo:     { band: 0.95, spin: 0.0,    ring: 0.18, ray: 0.55, core: 0.45, ripple: 0, rayRot: 0 },
    escuchando: { band: 0.92, spin: 0.0006, ring: 0.28, ray: 0.62, core: 0.55, ripple: 1, rayRot: 0 },
    pensando:   { band: 1.0,  spin: 0.006,  ring: 0.35, ray: 0.7,  core: 0.7,  ripple: 0, rayRot: 0.35 },
    hablando:   { band: 1.0,  spin: 0.0,    ring: 0.3,  ray: 0.75, core: 0.75, ripple: 0, rayRot: 0 },
  };

  const S = { ...TARGETS.reposo };
  if (REDUCED) for (const t of Object.values(TARGETS)) { t.spin = 0; t.rayRot = 0; t.ripple = 0; }
  let state = "reposo";
  let spin = 0, rayRot = 0;
  let life = { phase: "gone", t0: 0 };
  let running = false, hideTimer = null;

  // Audio de la voz de Tama (TTS) y nivel del micrófono.
  let ac = null, analyser = null, timeData = null, freqData = null, tts = null, micLevel = 0;
  const E = { vol: 0, low: 0, mid: 0, high: 0 };

  function audioContext() {
    if (!ac) {
      try {
        ac = new AudioContext();
        analyser = ac.createAnalyser();
        analyser.fftSize = 2048;
        analyser.smoothingTimeConstant = 0.35;
        analyser.connect(ac.destination);
        timeData = new Uint8Array(analyser.fftSize);
        freqData = new Uint8Array(analyser.frequencyBinCount);
      } catch (e) { ac = null; }
    }
    if (ac && ac.state === "suspended") ac.resume().catch(() => {});
    return ac;
  }

  function bandEnergy(f0, f1) {
    const hz = ac.sampleRate / analyser.fftSize;
    let s = 0, n = 0;
    for (let i = Math.floor(f0 / hz); i <= Math.ceil(f1 / hz) && i < freqData.length; i++) { s += freqData[i]; n++; }
    return n ? s / n / 255 : 0;
  }
  function readAudio() {
    analyser.getByteTimeDomainData(timeData); analyser.getByteFrequencyData(freqData);
    let sum = 0; for (let i = 0; i < timeData.length; i++) { const v = (timeData[i] - 128) / 128; sum += v * v; }
    return { vol: Math.min(1, Math.sqrt(sum / timeData.length) * 4.2), low: Math.min(1, bandEnergy(80, 300) * 1.6),
             mid: Math.min(1, bandEnergy(300, 2000) * 1.9), high: Math.min(1, bandEnergy(2000, 6000) * 3.2) };
  }
  // Si no se puede leer el audio, una voz simulada (sílabas y pausas).
  let syl = { end: 0, v: 0, l: 0, m: 0, h: 0 };
  function simulated(now) {
    if (now > syl.end) {
      const pause = Math.random() < 0.17, accent = Math.random() < 0.2;
      const v = pause ? 0.03 : (accent ? 0.85 : 0.4) + Math.random() * 0.25;
      syl = { end: now + (pause ? 200 + Math.random() * 280 : 90 + Math.random() * 140), v,
              l: v * (0.6 + Math.random() * 0.5), m: v * (0.7 + Math.random() * 0.4), h: pause ? 0 : Math.random() * v };
    }
    return { vol: syl.v, low: syl.l, mid: syl.m, high: syl.h };
  }
  function follow(key, x) { const k = x > E[key] ? 0.5 : 0.09; E[key] += (x - E[key]) * k; }

  let last = performance.now(), frames = 0, fpsT = last, slowFor = 0;

  function frame(now) {
    if (!running) return;
    const dt = Math.min(50, now - last); last = now;

    // Si va lento (celular), primero se quita un nivel de resplandor y luego se baja la densidad.
    frames++;
    if (now - fpsT > 1000) {
      const fps = (frames * 1000) / (now - fpsT); frames = 0; fpsT = now;
      view.dataset.fps = fps.toFixed(0);
      slowFor = fps < 45 ? slowFor + 1 : 0;
      if (slowFor >= 2) { slowFor = 0; if (quality > 1) quality--; else if (maxDpr > 1) { maxDpr = 1; resize(); } }
    }

    const T = TARGETS[state];
    const k = 1 - Math.pow(0.0025, dt / 1000);
    for (const key in T) S[key] += (T[key] - S[key]) * k;
    spin += S.spin * dt / 16.7; rayRot += S.rayRot * dt / 1000;

    let x;
    if (state === "hablando") x = tts && ac && ac.state === "running" ? readAudio() : simulated(now);
    else if (state === "escuchando") x = { vol: micLevel, low: micLevel * 0.8, mid: micLevel, high: micLevel * 0.3 };
    else if (state === "pensando") { const p = 0.5 + 0.5 * Math.sin(now / 240); x = { vol: 0.3 * p, low: 0.25 * p, mid: 0.35 * p, high: 0.1 }; }
    else { const p = 0.5 + 0.5 * Math.sin(now / 1100); x = { vol: 0.06 * p, low: 0.06 * p, mid: 0.05 * p, high: 0 }; }
    if (REDUCED) for (const key in x) x[key] *= 0.4;
    for (const key of ["vol", "low", "mid", "high"]) follow(key, x[key]);
    micLevel *= 0.9;

    const lt = now - life.t0;
    let grow = 1, bandOpen = 1, alpha = 1, flash = 0;
    if (life.phase === "appear") {
      const t = Math.min(1, lt / (REDUCED ? 400 : 1300));
      if (REDUCED) alpha = t;
      else {
        grow = 0.3 + 0.7 * easeOut(Math.min(1, t / 0.55));
        bandOpen = easeInOut(Math.max(0, (t - 0.2) / 0.8));
        flash = Math.max(0, 1 - t * 3) * 0.8;
        if (lt < 700 && Math.random() < 0.6) spawnSpark(0.25 + Math.random() * 0.25, true);
      }
      if (t >= 1) life = { phase: "visible", t0: now };
    } else if (life.phase === "bye") {
      const t = Math.min(1, lt / (REDUCED ? 400 : 1100));
      if (REDUCED) alpha = 1 - t;
      else {
        bandOpen = 1 - easeInOut(Math.min(1, t / 0.6));
        grow = 1 - 0.75 * easeIn(Math.max(0, (t - 0.35) / 0.65));
        alpha = 1 - easeIn(Math.max(0, (t - 0.7) / 0.3));
        flash = t > 0.6 && t < 0.8 ? (1 - Math.abs(t - 0.7) / 0.1) * 0.9 : 0;
      }
      if (t >= 1) life = { phase: "gone", t0: now };
    } else if (life.phase === "gone") { alpha = 0; }

    drawScene(now, grow, bandOpen, flash, dt);

    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalCompositeOperation = "source-over";
    ctx.clearRect(0, 0, W, H);
    ctx.globalAlpha = alpha;
    if (alpha > 0) {
      ctx.drawImage(scene, 0, 0);
      if (FILTER_OK && quality > 0) {
        ctx.globalCompositeOperation = "lighter";
        actx.globalCompositeOperation = "copy"; actx.filter = `blur(${2.2 * dpr}px)`;
        actx.drawImage(scene, 0, 0, bloomA.width, bloomA.height);
        ctx.globalAlpha = alpha * (0.42 + E.vol * 0.28);
        ctx.drawImage(bloomA, 0, 0, W, H);
        if (quality > 1) {
          bctx.globalCompositeOperation = "copy"; bctx.filter = `blur(${3.5 * dpr}px)`;
          bctx.drawImage(bloomA, 0, 0, bloomB.width, bloomB.height);
          ctx.globalAlpha = alpha * (0.32 + E.low * 0.25);
          ctx.drawImage(bloomB, 0, 0, W, H);
        }
      }
    }
    ctx.globalAlpha = 1; ctx.globalCompositeOperation = "source-over";
    requestAnimationFrame(frame);
  }

  // Rayos: un juego base que tiembla a ~12 fps (como la animación dibujada); el brillo va a 60.
  let rays = [], rayTick = 0;
  function newRays() {
    const n = 34;
    rays = Array.from({ length: n }, (_, i) => ({
      a: (i / n) * Math.PI * 2 + (Math.random() - 0.5) * 0.12,
      len: 0.78 + Math.random() * 0.22, w: 0.6 + Math.random() * 1.1, al: 0.4 + Math.random() * 0.5,
      ja: 0, jl: 0,
    }));
  }
  newRays();
  let sparks = [], flare = 0, flareA = 0;

  function ringPath(g, r) {
    g.beginPath();
    const pts = VERT.map((v, k) => { const a = -Math.PI / 2 + (k / SIDES) * Math.PI * 2 + v.a; return [Math.cos(a) * r * v.r, Math.sin(a) * r * v.r]; });
    for (let k = 0; k <= SIDES; k++) {
      const p = pts[k % SIDES], q = pts[(k + 1) % SIDES];
      if (k === 0) g.moveTo(p[0], p[1]);
      if (k < SIDES) {
        const mx = (p[0] + q[0]) / 2, my = (p[1] + q[1]) / 2, len = Math.hypot(mx, my), b = 1 + VERT[k].bulge;
        g.quadraticCurveTo(mx / len * len * b, my / len * len * b, q[0], q[1]);
      }
    }
    g.closePath();
  }

  // ---------------------------------------------------------------------------------------

  function spawnSpark(r0, burst) {
    sparks.push({ a: Math.random() * Math.PI * 2, r: R * r0, v: R * (burst ? 0.006 : 0.003) * (0.5 + Math.random()),
                  life: 1, size: 0.6 + Math.random() * 1.4, tw: Math.random() * 6 });
  }

  function drawScene(now, grow, bandOpen, flash, dt) {
    const g = sctx;
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.globalCompositeOperation = "source-over";
    g.globalAlpha = 1;
    g.clearRect(0, 0, W, H);
    g.translate(W / 2, H / 2);

    const ring = R * P.ringR * grow;
    const voice = E.vol, low = E.low, mid = E.mid, high = E.high;

    // Halo exterior suave.
    g.globalCompositeOperation = "lighter";
    const haloR = R * 1.32;
    const halo = g.createRadialGradient(0, 0, R * 0.5 * grow, 0, 0, haloR);
    halo.addColorStop(0, `rgba(${COLORS.halo},${0.07 + voice * 0.06})`);
    halo.addColorStop(0.6, `rgba(${COLORS.halo},${0.03 + voice * 0.03})`);
    halo.addColorStop(1, `rgba(${COLORS.halo},0)`);
    g.fillStyle = halo; g.beginPath(); g.arc(0, 0, haloR, 0, Math.PI * 2); g.fill();

    // Interior oscuro y translúcido (en la referencia se ve el paisaje a través).
    g.globalCompositeOperation = "source-over";
    const inner = g.createRadialGradient(0, 0, 0, 0, 0, R * P.interior * grow);
    inner.addColorStop(0, "rgba(120,100,26,0.55)");
    inner.addColorStop(0.5, COLORS.interior);
    inner.addColorStop(0.85, "rgba(70,58,16,0.55)");
    inner.addColorStop(1, "rgba(90,60,10,0.45)");
    g.fillStyle = inner; g.beginPath(); g.arc(0, 0, R * P.interior * grow, 0, Math.PI * 2); g.fill();

    // Banda de escritura: nace pequeña dentro del anillo y se abre hasta su lugar.
    if (bandOpen > 0.001 && bandSprite) {
      const scale = 0.32 + 0.68 * bandOpen; // al aparecer, la escritura va dentro del anillo
      const s = bandSprite.width * scale * (grow < 1 ? 1 : 1);
      g.save();
      g.rotate(spin);
      g.globalAlpha = Math.min(1, bandOpen * 1.6) * S.band * (0.85 + mid * 0.25);
      g.drawImage(bandSprite, -s / 2, -s / 2, s, s);
      // Brillo extra de la escritura que acompaña la voz.
      g.globalCompositeOperation = "lighter";
      g.globalAlpha = Math.min(1, bandOpen * 1.6) * (0.06 + mid * 0.22) * S.band;
      g.drawImage(bandSprite, -s / 2, -s / 2, s, s);
      g.restore();
      g.globalCompositeOperation = "source-over"; g.globalAlpha = 1;
    }

    // Rayos finos que salen del núcleo (temblor a ~12 fps).
    if (now > rayTick && !REDUCED) {
      rayTick = now + 83;
      for (const r of rays) { r.ja = (Math.random() - 0.5) * 0.05; r.jl = (Math.random() - 0.5) * (0.12 + high * 0.3); }
      if (Math.random() < 0.06 + high * 0.1) newRays();
      if (state === "hablando" && (mid > 0.65 || high > 0.6) && Math.random() < 0.35) { flare = 1; flareA = Math.random() < 0.5 ? -Math.PI / 2 : Math.random() * Math.PI * 2; }
    }
    g.globalCompositeOperation = "lighter";
    g.lineCap = "round";
    const rayLen = R * P.rayMax * grow * (0.82 + S.ray * 0.12 + mid * 0.1);
    for (const r of rays) {
      const a = r.a + r.ja + rayRot;
      const len = rayLen * Math.min(1.08, r.len + r.jl);
      const ex = Math.cos(a) * len, ey = Math.sin(a) * len;
      const grad = g.createLinearGradient(0, 0, ex, ey);
      const al = r.al * (0.45 + S.ray * 0.3 + mid * 0.35);
      grad.addColorStop(0, `rgba(255,255,240,${Math.min(1, al * 1.2)})`);
      grad.addColorStop(0.5, `rgba(${COLORS.ray},${al * 0.7})`);
      grad.addColorStop(1, `rgba(${COLORS.ray},0)`);
      g.strokeStyle = grad; g.lineWidth = r.w * dpr * (0.8 + mid * 0.4);
      g.beginPath(); g.moveTo(0, 0); g.lineTo(ex, ey); g.stroke();
    }

    // Destello ocasional (una línea de luz larga) en los acentos de la voz.
    if (flare > 0.01) {
      const len = R * 0.95 * flare;
      const grad = g.createLinearGradient(-Math.cos(flareA) * len, -Math.sin(flareA) * len, Math.cos(flareA) * len, Math.sin(flareA) * len);
      grad.addColorStop(0, "rgba(255,240,180,0)"); grad.addColorStop(0.5, `rgba(255,250,225,${0.75 * flare})`); grad.addColorStop(1, "rgba(255,240,180,0)");
      g.strokeStyle = grad; g.lineWidth = 1.6 * dpr;
      g.beginPath(); g.moveTo(-Math.cos(flareA) * len, -Math.sin(flareA) * len); g.lineTo(Math.cos(flareA) * len, Math.sin(flareA) * len); g.stroke();
      flare *= Math.pow(0.86, dt / 16.7);
    }

    // Núcleo sobreexpuesto.
    const coreR = R * (0.035 + S.core * 0.015 + mid * 0.03) * grow;
    let cg = g.createRadialGradient(0, 0, 0, 0, 0, coreR * 3.2);
    cg.addColorStop(0, `rgba(${COLORS.coreWarm},${0.45 + voice * 0.25})`);
    cg.addColorStop(0.4, `rgba(${COLORS.coreWarm},${0.12 + voice * 0.08})`);
    cg.addColorStop(1, `rgba(${COLORS.coreWarm},0)`);
    g.fillStyle = cg; g.beginPath(); g.arc(0, 0, coreR * 3.2, 0, Math.PI * 2); g.fill();
    cg = g.createRadialGradient(0, 0, 0, 0, 0, coreR);
    cg.addColorStop(0, "rgba(255,255,255,1)"); cg.addColorStop(0.6, `rgba(${COLORS.core},0.9)`); cg.addColorStop(1, `rgba(${COLORS.core},0)`);
    g.fillStyle = cg; g.beginPath(); g.arc(0, 0, coreR, 0, Math.PI * 2); g.fill();

    // Anillo blanco: grosor con los graves, brillo con el volumen.
    const thick = R * (P.ringMin + (P.ringMax - P.ringMin) * Math.min(1, low * 0.8 + voice * 0.3)) * grow;
    g.globalCompositeOperation = "lighter";
    g.lineJoin = "round";
    ringPath(g, ring);
    g.strokeStyle = `rgba(${COLORS.ringWarm},${0.16 + voice * 0.14})`; g.lineWidth = thick * 1.35; g.stroke();
    g.globalCompositeOperation = "source-over";
    ringPath(g, ring);
    g.strokeStyle = COLORS.ring; g.lineWidth = thick; g.stroke();
    // Borde interior apenas más cálido, como en la referencia.
    ringPath(g, ring - thick * 0.42);
    g.strokeStyle = "rgba(255,246,200,0.5)"; g.lineWidth = Math.max(1, thick * 0.12); g.stroke();

    // Destello de aparición o despedida.
    if (flash > 0) {
      g.globalCompositeOperation = "lighter";
      const fg = g.createRadialGradient(0, 0, 0, 0, 0, R * 0.9);
      fg.addColorStop(0, `rgba(255,250,220,${flash})`); fg.addColorStop(1, "rgba(255,250,220,0)");
      g.fillStyle = fg; g.beginPath(); g.arc(0, 0, R * 0.9, 0, Math.PI * 2); g.fill();
    }

    // Escuchando: ondas suaves que entran hacia el centro.
    if (S.ripple > 0.02) {
      g.globalCompositeOperation = "lighter";
      for (let i = 0; i < 3; i++) {
        const p = (now / 1800 + i / 3) % 1;
        g.strokeStyle = `rgba(255,214,130,${0.20 * p * S.ripple * (1 + voice)})`;
        g.lineWidth = R * 0.008;
        g.beginPath(); g.arc(0, 0, R * (1.28 - p * 0.42), 0, Math.PI * 2); g.stroke();
      }
    }

    // Chispas sutiles: pocas, titilan y se alejan.
    if (!REDUCED && Math.random() < 0.04 + high * 0.25 * (state === "hablando" ? 1 : 0.3)) spawnSpark(0.1 + Math.random() * 0.4, false);
    g.globalCompositeOperation = "lighter";
    sparks = sparks.filter((s) => s.life > 0);
    for (const s of sparks) {
      s.r += s.v * dt / 16.7; s.life -= 0.012 * dt / 16.7;
      const tw = 0.5 + 0.5 * Math.sin(now / 90 + s.tw);
      g.fillStyle = `rgba(255,236,160,${s.life * tw})`;
      const x = Math.cos(s.a) * s.r, y = Math.sin(s.a) * s.r, z = s.size * dpr;
      g.fillRect(x - z * 2, y - 0.4 * dpr, z * 4, 0.8 * dpr); g.fillRect(x - 0.4 * dpr, y - z * 2, 0.8 * dpr, z * 4);
    }
  }

  function easeOut(t) { return 1 - Math.pow(1 - t, 3); }
  function easeIn(t) { return t * t * t; }
  function easeInOut(t) { return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; }


  // --- lo que usa voz.js ----------------------------------------------------------------------
  const STATES = { idle: "reposo", abriendo: "escuchando", escuchando: "escuchando",
                   procesando: "pensando", hablando: "hablando" };

  function show() {
    clearTimeout(hideTimer);
    audioContext(); // se crea con el toque en Hablar, así el navegador deja que suene
    lineRenzo.textContent = ""; lineTama.textContent = "";
    view.hidden = false;
    document.body.classList.add("con-raphael");
    resize();
    life = { phase: "appear", t0: performance.now() };
    view.dataset.estado = state = "escuchando";
    if (!running) { running = true; last = performance.now(); requestAnimationFrame(frame); }
  }

  function hide() {
    if (view.hidden) return;
    life = { phase: "bye", t0: performance.now() };
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
      running = false;
      view.hidden = true;
      document.body.classList.remove("con-raphael");
      tts = null;
    }, REDUCED ? 450 : 1150);
  }

  function setState(voiceState) {
    const next = STATES[voiceState] || "reposo";
    state = next;
    view.dataset.estado = next;
  }

  // Conecta el audio que ya está sonando al analizador, sin cortarlo: pasa por el mismo
  // contexto hacia los parlantes. Si el contexto no corre, la voz sigue igual y la figura simula.
  function attach(audio) {
    const context = audioContext();
    if (!context || context.state !== "running") return;
    try {
      const source = context.createMediaElementSource(audio);
      source.connect(analyser);
      tts = source;
      const done = () => { if (tts === source) tts = null; };
      audio.addEventListener("ended", done);
      audio.addEventListener("pause", done);
    } catch (e) { tts = null; }
  }

  view.addEventListener("click", () => document.dispatchEvent(new CustomEvent("tama:raphael-toque")));
  window.addEventListener("resize", () => { if (running) resize(); });

  window.TamaRaphael = {
    show, hide, state: setState, attach,
    input(level) { micLevel = Math.max(micLevel, Math.min(1, level * 9)); },
    renzo(text) { lineRenzo.textContent = text ? "Tú: " + text : ""; },
    tama(text) { lineTama.textContent = text || ""; },
    get visible() { return !view.hidden; },
  };
})();
