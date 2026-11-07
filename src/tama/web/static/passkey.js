// Passkeys (WebAuthn) para entrar a Tama desde fuera del PC. Solo usa la API del navegador:
// nada se pide a internet; el servidor de Tama verifica todo.
const tamaPasskey = (() => {
  const estado = () => document.getElementById("estado");
  const say = (text) => { estado().textContent = text; };

  function toBytes(value) {
    const base = value.replace(/-/g, "+").replace(/_/g, "/");
    const padded = base + "=".repeat((4 - (base.length % 4)) % 4);
    return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0)).buffer;
  }

  function toText(buffer) {
    let binary = "";
    for (const byte of new Uint8Array(buffer)) binary += String.fromCharCode(byte);
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  async function post(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
      credentials: "same-origin",
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "No pude hablar con Tama.");
    return data;
  }

  function supported() {
    if (window.PublicKeyCredential) return true;
    say("Este navegador no soporta passkeys. Abre Tama en Chrome o Safari actualizados.");
    return false;
  }

  async function entrar() {
    if (!supported()) return;
    try {
      say("Preparando…");
      const { flow, options } = await post("/auth/entrar/opciones");
      options.challenge = toBytes(options.challenge);
      for (const c of options.allowCredentials || []) c.id = toBytes(c.id);
      say("Confirma con tu huella, rostro o PIN…");
      const cred = await navigator.credentials.get({ publicKey: options });
      await post("/auth/entrar", {
        flow,
        credential: {
          id: cred.id,
          rawId: toText(cred.rawId),
          type: cred.type,
          response: {
            clientDataJSON: toText(cred.response.clientDataJSON),
            authenticatorData: toText(cred.response.authenticatorData),
            signature: toText(cred.response.signature),
            userHandle: cred.response.userHandle ? toText(cred.response.userHandle) : null,
          },
          clientExtensionResults: cred.getClientExtensionResults(),
        },
      });
      say("Listo.");
      location.href = "/";
    } catch (error) {
      say(error.name === "NotAllowedError" ? "Cancelado. Vuelve a intentarlo." : error.message);
    }
  }

  async function registrar(code, name) {
    if (!supported()) return;
    try {
      say("Revisando el código…");
      const { flow, options } = await post("/auth/registrar/opciones", { code });
      options.challenge = toBytes(options.challenge);
      options.user.id = toBytes(options.user.id);
      for (const c of options.excludeCredentials || []) c.id = toBytes(c.id);
      say("Crea la passkey con tu huella, rostro o PIN…");
      const cred = await navigator.credentials.create({ publicKey: options });
      await post("/auth/registrar", {
        flow,
        code,
        name: name || navigator.platform || "Celular",
        credential: {
          id: cred.id,
          rawId: toText(cred.rawId),
          type: cred.type,
          response: {
            clientDataJSON: toText(cred.response.clientDataJSON),
            attestationObject: toText(cred.response.attestationObject),
            transports: cred.response.getTransports ? cred.response.getTransports() : [],
          },
          clientExtensionResults: cred.getClientExtensionResults(),
        },
      });
      say("Passkey registrada. Ahora entra con ella.");
      setTimeout(() => { location.href = "/entrar"; }, 1200);
    } catch (error) {
      say(error.name === "NotAllowedError" ? "Cancelado. Vuelve a intentarlo." : error.message);
    }
  }

  return { entrar, registrar };
})();
