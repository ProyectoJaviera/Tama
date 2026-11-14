"""Servidor web: chat por SSE y paneles de memoria, objetivos y diario.

Un solo usuario y una sola conversación. El turno corre en un hilo con el motor de `tama.chat`; sus
eventos llegan al navegador por una cola, y las confirmaciones esperan la respuesta de Renzo.
"""

import hashlib
import json
import os
import queue
import secrets
import sys
import threading
import time
import wave
from collections.abc import Callable, Iterator
from datetime import date, datetime
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from tama import agent, chat, commands, history
from tama import brain as brain_module
from tama import voice as voice_module
from tama.auth import CODE_MINUTES, Access, AuthError
from tama.brain import Brain
from tama.cascade import Answered, Switch
from tama.config import Settings
from tama.mcp_servers import memoria
from tama.memory import MAX_PROFILE_CHARS, session_prompt
from tama.ollama import OllamaError
from tama.proactive import act_on
from tama.providers import Error, Message, Text, ToolCall
from tama.reflection import diary_path, write_diary
from tama.voice import VoiceError

HERE = Path(__file__).parent
CARD_PAGE = 20  # datos por página en la ficha de la web
# Cambia con el contenido de static/: el navegador no se queda con un app.js viejo tras actualizar.
STATIC_VERSION = hashlib.sha256(
    b"".join(p.read_bytes() for p in sorted((HERE / "static").iterdir()) if p.is_file())
).hexdigest()[:10]
NOTICE_LABELS = {
    "mostrado": "sin responder",
    "ignorado": "sin respuesta",
    "listo": "listo",
    "pospuesto": "pospuesto",
    "silenciado": "no se repite",
}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "testserver"}
# La agrega `tailscale serve` con la cuenta de quien abre Tama; nadie fuera de Tailscale la envía,
# porque Tama solo escucha en 127.0.0.1.
TAILSCALE_USER = "tailscale-user-login"
END = object()
MAX_AUDIO_BYTES = 16000 * 2 * 60  # un minuto de WAV de 16 kHz y 16 bits
SESSION_COOKIE = "tama_sesion"
# Sin datos: se sirven sin sesión también desde fuera del PC.
PUBLIC_PATHS = ("/sw.js", "/manifest.webmanifest", "/entrar", "/registrar")
PUBLIC_PREFIXES = ("/static/", "/auth/")
REMOTE_FORGET = "Olvidar para siempre solo se puede desde el PC."


class Turn:
    """Un turno en curso: sus eventos y la confirmación que esté esperando."""

    def __init__(self, timeout_s: float) -> None:
        self.events: queue.Queue = queue.Queue()
        self.timeout_s = timeout_s
        self.answer: queue.Queue = queue.Queue()
        self.question_id = 0

    def send(self, kind: str, **data: Any) -> None:
        self.events.put({"kind": kind, **data})

    def confirm(self, call: ToolCall, info: Any) -> bool:
        """Nada destructivo corre sin un Sí; si se agota el tiempo, cuenta como No."""
        self.question_id += 1
        self.send("confirm", id=self.question_id, question=chat.confirm_question(call))
        try:
            answer = self.answer.get(timeout=self.timeout_s)
        except queue.Empty:
            self.send("confirm_closed", id=self.question_id)
            return False
        return answer is True


DAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def day_label(day: date, today: date) -> str:
    """Separador de día en el chat: "Hoy", "Ayer" o "jueves 12 de noviembre"."""
    if day == today:
        return "Hoy"
    if (today - day).days == 1:
        return "Ayer"
    return f"{DAYS[day.weekday()]} {day.day} de {MONTHS[day.month - 1]}"


def stream_turn(
    brain: Brain,
    session: "Session",
    turn: Turn,
    text: str,
    turn_id: str = "",
    remote: bool = False,
) -> None:
    answered: Answered | None = None
    try:
        for event in chat.run_turn(
            brain,
            session.messages,
            text,
            session.max_tokens,
            session.max_tools,
            turn.confirm,
            remote,
        ):
            if isinstance(event, Text):
                turn.send("text", text=chat.without_emojis(event.text))
            elif isinstance(event, Switch):
                note = f"{event.frm} no respondió ({event.reason}); seguí en {event.to}"
                turn.send("note", text=note)
            elif isinstance(event, agent.ToolUse):
                turn.send("tool", name=event.name, server=event.server, args=event.arguments)
            elif isinstance(event, agent.ToolResult):
                turn.send("tool_result", text=event.text[:400], ok=event.ok, skipped=event.skipped)
            elif isinstance(event, Answered):
                answered = event
            elif isinstance(event, Error):
                turn.send("error", text=f"No pude responder: {event.message}")
            elif isinstance(event, agent.FinalAnswer) and answered is not None:
                line = chat.speed_line(answered)
                if answered.skipped:
                    line += " · sin usar: " + "; ".join(answered.skipped)
                turn.send("done", line=line)
    except Exception as exc:  # un error no puede dejar el chat esperando para siempre
        turn.send("error", text=f"No pude responder: {exc}")
    finally:
        turn.events.put(END)
        session.finish(turn_id)


class Session:
    """La conversación abierta en el navegador."""

    def __init__(self, brain: Brain, settings: Settings) -> None:
        suggestion = ""
        if brain.tools is not None:
            outcome = brain.tools.call("next_suggestion", {})
            if outcome.ok and outcome.text != memoria.NO_SUGGESTION:
                suggestion = outcome.text
        system = session_prompt(settings.memory_dir, brain.store, suggestion)
        self.messages: list[Message] = [{"role": "system", "content": system}]
        self.max_tokens = settings.history_max_tokens
        self.max_tools = settings.tools_max
        self.timeout_s = settings.web_confirm_timeout_s
        self.turns: dict[str, Turn] = {}
        # Lo que espera un Sí de Renzo: ("olvidar", datos) o ("cancelar", número).
        self.pending: tuple[str, Any] | None = None
        self.brain = brain
        self.turn_limit_s = settings.web_turn_limit_s
        self._guard = threading.Lock()
        self._current: tuple[str, float] | None = None
        """El turno en curso y cuándo empezó."""

    def busy(self) -> bool:
        with self._guard:
            return self._running(time.monotonic())

    def _running(self, now: float) -> bool:
        # Pasado el límite, el turno se da por perdido: el chat nunca queda bloqueado.
        return self._current is not None and now - self._current[1] < self.turn_limit_s

    def running_for(self) -> float:
        with self._guard:
            return time.monotonic() - self._current[1] if self._current else 0.0

    def start(self, text: str, remote: bool = False) -> str | None:
        """Lanza un turno; devuelve su id, o None si ya hay uno en curso."""
        with self._guard:
            now = time.monotonic()
            if self._running(now):
                return None
            turn_id = secrets.token_hex(8)
            self._current = (turn_id, now)
        if self.brain.reflector is not None:
            self.brain.reflector.activity()
        turn = self.turns[turn_id] = Turn(self.timeout_s)
        threading.Thread(
            target=stream_turn,
            args=(self.brain, self, turn, text, turn_id, remote),
            daemon=True,
        ).start()
        return turn_id

    def finish(self, turn_id: str = "") -> None:
        if self.brain.reflector is not None:
            self.brain.reflector.activity()
        if self.brain.proactive is not None:
            self.brain.proactive.activity()
        with self._guard:
            # Un turno que se pasó del límite no libera el de otro que ya empezó.
            if self._current is not None and self._current[0] in (turn_id, ""):
                self._current = None


def events_of(turn: Turn) -> Iterator[str]:
    while True:
        try:
            event = turn.events.get(timeout=15)
        except queue.Empty:
            yield ": sigue activo\n\n"
            continue
        if event is END:
            yield "event: end\ndata: {}\n\n"
            return
        yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


def create_app(brain: Brain, settings: Settings) -> FastAPI:
    app = FastAPI(title="Tama", docs_url=None, redoc_url=None, openapi_url=None)
    os.environ["TAMA_DATA_DIR"] = str(settings.data_dir.resolve())
    os.environ["TAMA_MEMORY_DIR"] = str(settings.memory_dir.resolve())
    templates = Jinja2Templates(directory=HERE / "templates")
    csrf = secrets.token_urlsafe(32)
    session = Session(brain, settings)
    app.state.session = session
    access = Access(settings.data_dir / "acceso.db")
    app.state.access = access
    online = brain.connectivity.online if brain.connectivity is not None else (lambda: True)
    voice = voice_module.build(settings, online) if settings.voice_enabled else None
    app.state.voice = voice
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.middleware("http")
    async def only_localhost(request: Request, call_next):
        # Evita que un sitio externo llegue al servidor con un dominio propio (DNS rebinding).
        # Desde el celular solo vale el nombre del PC en Tailscale, por `tailscale serve`.
        refused = rejection(request, settings)
        if refused:
            message, detail = refused
            # El detalle (con lo esperado) va solo a la consola del PC, no a quien pregunta.
            print(f"Tama rechazó una conexión: {detail}", file=sys.stderr, flush=True)
            return PlainTextResponse(message, status_code=403)
        # Desde fuera del PC hace falta una sesión abierta con passkey (Sprint 10), salvo para
        # lo que no tiene datos: la interfaz, la PWA y las páginas para entrar.
        request.state.remote = is_remote(request)
        if (
            request.state.remote
            and settings.web_login_remote
            and not is_public(request.url.path)
            and not access.valid_session(request.cookies.get(SESSION_COOKIE, ""))
        ):
            if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
                return RedirectResponse("/entrar", status_code=303)
            return PlainTextResponse("Inicia sesión con tu passkey en /entrar.", status_code=401)
        return await call_next(request)

    @app.get("/sw.js")
    def service_worker() -> Response:
        """En la raíz, para controlar todo el sitio; solo guarda la interfaz (ver static/sw.js)."""
        text = (HERE / "static" / "sw.js").read_text(encoding="utf-8")
        return Response(
            text.replace("__VERSION__", STATIC_VERSION),
            media_type="text/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/manifest.webmanifest")
    def manifest() -> Response:
        text = (HERE / "static" / "manifest.webmanifest").read_text(encoding="utf-8")
        return Response(text, media_type="application/manifest+json")

    def check_csrf(request: Request) -> None:
        if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), csrf):
            raise HTTPException(status_code=403, detail="Falta el token CSRF")

    def page(request: Request, name: str, **context: Any) -> HTMLResponse:
        context = {"csrf": csrf, "v": STATIC_VERSION, **context}
        return templates.TemplateResponse(request, name, context)

    def remote(request: Request) -> bool:
        return getattr(request.state, "remote", False)

    def local_only(request: Request) -> None:
        if remote(request):
            raise HTTPException(status_code=403, detail="Eso solo se puede hacer desde el PC.")

    def origin_of(request: Request) -> tuple[str, str]:
        """RP ID y origen de WebAuthn: el nombre con que el celular abrió Tama."""
        host = request.headers.get("host", "")
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        return host.rsplit(":", 1)[0].lower(), f"{proto}://{host}"

    @app.get("/entrar", response_class=HTMLResponse)
    def login_page(request: Request) -> HTMLResponse:
        return page(request, "entrar.html")

    @app.get("/registrar", response_class=HTMLResponse)
    def register_page(request: Request) -> HTMLResponse:
        return page(request, "registrar.html")

    @app.post("/auth/entrar/opciones")
    async def login_options(request: Request) -> Response:
        rp_id, _ = origin_of(request)
        try:
            flow, options = access.login_options(rp_id)
        except AuthError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"flow": flow, "options": json.loads(options)})

    @app.post("/auth/entrar")
    async def login(request: Request) -> Response:
        data = await request.json()
        rp_id, origin = origin_of(request)
        try:
            token = access.login(
                str(data.get("flow", "")),
                data.get("credential") or {},
                rp_id,
                origin,
                settings.web_session_days,
            )
        except AuthError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        response = JSONResponse({"ok": True})
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=int(settings.web_session_days * 86400),
            httponly=True,
            secure=origin.startswith("https://"),
            samesite="strict",
            path="/",
        )
        return response

    @app.post("/auth/registrar/opciones")
    async def register_options(request: Request) -> Response:
        data = await request.json()
        rp_id, _ = origin_of(request)
        try:
            flow, options = access.registration_options(rp_id, str(data.get("code", "")))
        except AuthError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"flow": flow, "options": json.loads(options)})

    @app.post("/auth/registrar")
    async def register(request: Request) -> Response:
        data = await request.json()
        rp_id, origin = origin_of(request)
        try:
            key = access.register(
                str(data.get("flow", "")),
                data.get("credential") or {},
                rp_id,
                origin,
                str(data.get("name", "")),
                str(data.get("code", "")),
            )
        except AuthError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True, "name": key.name})

    @app.post("/auth/salir", dependencies=[Depends(check_csrf)])
    def logout(request: Request) -> Response:
        access.logout(request.cookies.get(SESSION_COOKIE, ""))
        # Desde HTMX la página entera debe ir a /entrar, no solo el botón.
        response = Response(status_code=204, headers={"HX-Redirect": "/entrar"})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.post("/acceso/codigo", dependencies=[Depends(check_csrf), Depends(local_only)])
    def new_code() -> HTMLResponse:
        code = access.new_code()
        return HTMLResponse(
            f"Código: <strong>{code[:4]} {code[4:]}</strong> (vale {CODE_MINUTES} minutos, una "
            'vez). En el celular abre Tama y toca "Registrar esta passkey".'
        )

    @app.post(
        "/acceso/revocar/{passkey_id}", dependencies=[Depends(check_csrf), Depends(local_only)]
    )
    def revoke(request: Request, passkey_id: int) -> HTMLResponse:
        access.revoke(passkey_id)
        return page(request, "passkeys.html", passkeys=access.passkeys())

    @app.post("/voz/escuchar", dependencies=[Depends(check_csrf)])
    async def listen(request: Request) -> Response:
        """Audio WAV (16 kHz, mono) desde el micrófono del navegador → texto con whisper.cpp."""
        if voice is None or voice.listener is None:
            return JSONResponse({"error": "La voz está apagada."}, status_code=503)
        audio = await request.body()
        if len(audio) > MAX_AUDIO_BYTES:
            return JSONResponse({"error": "El audio es demasiado largo."}, status_code=413)
        try:
            text = await run_in_threadpool(voice.listener.transcribe, audio)
        except (VoiceError, wave.Error) as exc:
            return JSONResponse({"error": str(exc) or "No pude leer el audio."}, status_code=400)
        return JSONResponse({"text": text})

    @app.post("/voz/hablar", dependencies=[Depends(check_csrf)])
    def speak(texto: str = Form(...)) -> Response:
        if voice is None:
            return JSONResponse({"error": "La voz está apagada."}, status_code=503)
        try:
            speech = voice.speak(texto)
        except VoiceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        label = "ElevenLabs" if speech.provider == "elevenlabs" else "Piper (local)"
        return Response(speech.audio, media_type=speech.media_type, headers={"X-Tama-Voz": label})

    def chat_history() -> list[tuple[str, str]]:
        """La conversación, con los comandos y los avisos, tal como se vio. Cada día empieza con
        su fecha: sin ella, el resumen matutino de dos días distintos parecía repetido."""
        types = ("user_msg", "assistant_msg", "command", "proactive")
        shown: list[tuple[str, str]] = []
        today = memoria.local_now().date()
        last_day = None
        for kind, content, ts in brain.store.recent_messages_at(40, types):
            day = datetime.fromisoformat(ts).astimezone(memoria.local_now().tzinfo).date()
            if day != last_day:
                shown.append(("day", day_label(day, today)))
                last_day = day
            if kind == "command":
                item = json.loads(content)
                if item["texto"]:
                    shown.append(("user", item["texto"]))
                shown.append(("command", item["respuesta"]))
                # Los botones vuelven solo para los recordatorios que siguen pendientes.
                still = [
                    c
                    for c in item.get("opciones", [])
                    if memoria.is_pending(int(c["command"].rsplit(" ", 1)[1]))
                ]
                if still:
                    shown.append(("choices", still))
            elif kind == "proactive":
                shown.append(("notice", f"Aviso · {content}"))
            else:
                shown.append(("user" if kind == "user_msg" else "assistant", content))
        return shown

    def answer_command(
        text: str, reply: str, confirm: str = "", choices: list | None = None
    ) -> dict[str, Any]:
        """Responde un comando y lo guarda, con sus opciones, para que siga en el chat al volver."""
        item = {"texto": text, "respuesta": reply, "opciones": choices or []}
        brain.store.log("command", json.dumps(item, ensure_ascii=False))
        answer: dict[str, Any] = {"reply": reply}
        if confirm:
            answer["confirm"] = confirm
        if choices:
            answer["choices"] = choices
        return answer

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        return page(request, "chat.html", history=chat_history(), commands=commands.COMMANDS)

    @app.post("/chat", dependencies=[Depends(check_csrf)])
    def send(request: Request, text: str = Form(...)) -> dict[str, Any]:
        text = text.strip()
        if not text:
            raise HTTPException(status_code=422, detail="Mensaje vacío")
        if text.startswith("/"):
            command = commands.canonical(text)
            if command.startswith("/olvidar") and remote(request):
                return answer_command(text, REMOTE_FORGET)
            if command.startswith("/olvidar"):
                preview, arguments = commands.forget_preview(brain, command[8:].strip())
                if arguments is None:
                    return answer_command(text, preview)
                session.pending = ("olvidar", arguments)
                return answer_command(text, preview, confirm="¿Lo olvido para siempre?")
            if command.startswith("/cancelar"):
                preview, found = commands.cancel_preview(command[9:].strip())
                if len(found) == 1:
                    session.pending = ("cancelar", found[0]["id"])
                    return answer_command(text, preview, confirm=preview)
                choices = [
                    {"label": memoria.reminder_line(r), "command": f"/cancelar {r['id']}"}
                    for r in found
                ]
                return answer_command(text, preview, choices=choices)
            return answer_command(text, commands.run(brain, command, settings.memory_dir))
        turn_id = session.start(text, remote(request))
        if turn_id is None:
            waited = int(session.running_for())
            raise HTTPException(
                status_code=409,
                detail=f"Tama sigue con tu mensaje anterior (lleva {waited} s). Espera a que "
                "termine.",
            )
        return {"turn": turn_id}

    @app.post("/chat/comando/confirmar", dependencies=[Depends(check_csrf)])
    def confirm_command(respuesta: str = Form("no")) -> dict[str, str]:
        pending, session.pending = session.pending, None
        if pending is None:
            return answer_command("", "No había nada por confirmar.")
        kind, data = pending
        if respuesta != "si":
            reply = "No se borró nada." if kind == "olvidar" else "No se canceló."
        elif kind == "olvidar":
            reply = brain.tools.call("forget_memory", data).text
        else:
            reply = memoria.cancel_one(data)
        return answer_command("", reply)

    @app.get("/chat/{turn_id}/eventos")
    def stream(turn_id: str) -> StreamingResponse:
        turn = session.turns.get(turn_id)
        if turn is None:
            raise HTTPException(status_code=404)
        return StreamingResponse(events_of(turn), media_type="text/event-stream")

    @app.post("/chat/{turn_id}/confirmar", dependencies=[Depends(check_csrf)])
    def answer(turn_id: str, respuesta: str = Form(...)) -> Response:
        turn = session.turns.get(turn_id)
        if turn is None:
            raise HTTPException(status_code=404)
        turn.answer.put(respuesta == "si")
        return Response(status_code=204)

    # --- memoria ---------------------------------------------------------------------------

    def profile_path() -> Path:
        return settings.memory_dir / "nucleo.md"

    def read_profile() -> str:
        path = profile_path()
        return path.read_text(encoding="utf-8") if path.exists() else ""

    @app.get("/memoria", response_class=HTMLResponse)
    def memory(request: Request, q: str = "") -> HTMLResponse:
        name = "fichas.html" if request.headers.get("hx-request") else "memoria.html"
        return page(
            request,
            name,
            cards=memoria.list_cards(q),
            q=q,
            profile=read_profile(),
            limit=MAX_PROFILE_CHARS,
        )

    def memory_page(request: Request, entity_id: int, number: int) -> HTMLResponse:
        """La pestaña Memoria con una ficha ya abierta: se puede enlazar por su nombre."""
        found = memoria.card(entity_id, number, CARD_PAGE)
        others = [c for c in memoria.list_cards() if c["id"] != entity_id]
        return page(
            request,
            "memoria.html",
            cards=memoria.list_cards(),
            q="",
            profile=read_profile(),
            limit=MAX_PROFILE_CHARS,
            card=found,
            others=others,
        )

    @app.post("/memoria/perfil", dependencies=[Depends(check_csrf)])
    def save_profile(request: Request, texto: str = Form("")) -> HTMLResponse:
        if len(texto.strip()) > MAX_PROFILE_CHARS:
            raise HTTPException(status_code=422, detail="El perfil es demasiado largo")
        profile_path().parent.mkdir(parents=True, exist_ok=True)
        profile_path().write_text(texto.strip() + "\n", encoding="utf-8")
        return HTMLResponse("<p class='ok'>Perfil guardado.</p>")

    def show_card(request: Request, entity_id: int, number: int = 1) -> HTMLResponse:
        found = memoria.card(entity_id, number, CARD_PAGE)
        if found is None:
            return HTMLResponse("<p>Esa ficha ya no existe.</p>")
        others = [c for c in memoria.list_cards() if c["id"] != entity_id]
        return page(request, "ficha.html", card=found, others=others)

    @app.get("/memoria/ficha/{ref}", response_class=HTMLResponse)
    def card_detail(request: Request, ref: str, pagina: int = 1) -> HTMLResponse:
        """La ficha se pide por su nombre (/memoria/ficha/Lucía); el número también sirve."""
        entity_id = memoria.find_card(ref)
        if entity_id is None:
            return HTMLResponse(f"<p>No encontré una ficha llamada «{escape(ref)}».</p>")
        if not request.headers.get("hx-request"):
            return memory_page(request, entity_id, pagina)
        return show_card(request, entity_id, pagina)

    @app.get("/memoria/linea", response_class=PlainTextResponse)
    def timeline(tema: str = "") -> str:
        return memoria.linea_de_tiempo(tema)

    @app.post("/memoria/ficha/{entity_id}/dato/{fact}", dependencies=[Depends(check_csrf)])
    def edit_fact(
        request: Request, entity_id: int, fact: int, texto: str = Form(...)
    ) -> HTMLResponse:
        memoria.replace_fact(fact, texto)
        return show_card(request, entity_id)

    @app.post("/memoria/ficha/{entity_id}/olvidar/{fact}", dependencies=[Depends(check_csrf)])
    def forget_fact(request: Request, entity_id: int, fact: int) -> HTMLResponse:
        if remote(request):
            return HTMLResponse(f"<p>{REMOTE_FORGET}</p>", status_code=403)
        memoria.forget_memory([], [fact])
        return show_card(request, entity_id)

    @app.post("/memoria/ficha/{entity_id}/renombrar", dependencies=[Depends(check_csrf)])
    def rename(request: Request, entity_id: int, nombre: str = Form(...)) -> HTMLResponse:
        found = memoria.card(entity_id)
        if found is None or not memoria.rename_card(found["name"], nombre):
            raise HTTPException(status_code=409, detail="No se pudo renombrar")
        return show_card(request, entity_id)

    @app.post("/memoria/ficha/{entity_id}/fusionar", dependencies=[Depends(check_csrf)])
    def merge(request: Request, entity_id: int, con: int = Form(...)) -> HTMLResponse:
        cards = {c["id"]: c["name"] for c in memoria.list_cards()}
        if entity_id not in cards or con not in cards:
            raise HTTPException(status_code=404)
        if not memoria.merge_entities(cards[entity_id], cards[con], manual=True):
            raise HTTPException(status_code=409, detail="Renzo no se fusiona")
        return show_card(request, entity_id)

    @app.post("/memoria/ficha/{entity_id}/separar", dependencies=[Depends(check_csrf)])
    def split(
        request: Request,
        entity_id: int,
        nombre: str = Form(...),
        datos: list[int] = Form(default_factory=list),  # noqa: B008
    ) -> HTMLResponse:
        memoria.split_card(entity_id, datos, nombre)
        return show_card(request, entity_id)

    # --- objetivos -------------------------------------------------------------------------

    def goals_list(request: Request) -> HTMLResponse:
        return page(request, "objetivos_lista.html", goals=memoria.goals_by_status())

    @app.get("/objetivos", response_class=HTMLResponse)
    def goals(request: Request) -> HTMLResponse:
        return page(request, "objetivos.html", goals=memoria.goals_by_status())

    @app.post("/objetivos", dependencies=[Depends(check_csrf)])
    def create(
        request: Request, titulo: str = Form(...), detalle: str = Form(""), fecha: str = Form("")
    ) -> HTMLResponse:
        memoria.create_goal(titulo, detalle, fecha)
        return goals_list(request)

    @app.post("/objetivos/{goal}/avance", dependencies=[Depends(check_csrf)])
    def progress(request: Request, goal: int, avance: str = Form(...)) -> HTMLResponse:
        memoria.add_goal_progress(str(goal), avance)
        return goals_list(request)

    @app.post("/objetivos/{goal}/editar", dependencies=[Depends(check_csrf)])
    def edit_goal(
        request: Request,
        goal: int,
        titulo: str = Form(""),
        detalle: str = Form(""),
        fecha: str = Form(""),
        estado: str = Form(""),
    ) -> HTMLResponse:
        memoria.update_goal(str(goal), titulo, detalle, fecha, estado)
        return goals_list(request)

    @app.post("/objetivos/{goal}/descartar", dependencies=[Depends(check_csrf)])
    def discard(request: Request, goal: int) -> HTMLResponse:
        memoria.discard_goal(str(goal))
        return goals_list(request)

    # --- avisos ----------------------------------------------------------------------------

    watchers: list[queue.Queue] = []
    if brain.proactive is not None:
        brain.proactive.busy = session.busy

        def broadcast(notice: dict) -> None:
            for watcher in list(watchers):
                watcher.put(notice)

        brain.proactive.listeners.append(broadcast)

    def watch() -> Iterator[str]:
        watcher: queue.Queue = queue.Queue()
        watchers.append(watcher)
        try:
            while True:
                try:
                    notice = watcher.get(timeout=15)
                except queue.Empty:
                    yield ": sigue activo\n\n"
                    continue
                yield f"data: {json.dumps(notice, ensure_ascii=False, default=str)}\n\n"
        finally:
            watchers.remove(watcher)

    @app.get("/avisos/eventos")
    def notice_stream() -> StreamingResponse:
        return StreamingResponse(watch(), media_type="text/event-stream")

    def notices_page(request: Request, name: str) -> HTMLResponse:
        return page(
            request,
            name,
            notices=memoria.list_notices(),
            reminders=memoria.list_reminders(),
            states=NOTICE_LABELS,
        )

    @app.get("/avisos", response_class=HTMLResponse)
    def notices(request: Request) -> HTMLResponse:
        return notices_page(request, "avisos.html")

    @app.get("/avisos/pendientes")
    def pending_notices() -> dict[str, int]:
        return {"pendientes": memoria.notice_count()}

    @app.get("/avisos/abiertos")
    def open_notices() -> list[dict[str, Any]]:
        """Recordatorios avisados sin respuesta: la franja fija de todas las pestañas."""
        return [
            {"id": n["id"], "text": n["text"], "urgent": bool(n["urgent"])}
            for n in memoria.open_reminders()
        ]

    @app.post("/avisos/{notice}/{action}", dependencies=[Depends(check_csrf)])
    def act(request: Request, notice: int, action: str) -> HTMLResponse:
        if action not in ("listo", "posponer", "silenciar", "util", "molesto"):
            raise HTTPException(status_code=404)
        act_on(notice, action, memoria.local_now())
        return notices_page(request, "avisos_lista.html")

    # --- diario y estado -------------------------------------------------------------------

    @app.get("/diario", response_class=HTMLResponse)
    def diary(request: Request, fecha: str = "") -> HTMLResponse:
        try:
            day = date.fromisoformat(fecha) if fecha else date.today()
        except ValueError:
            day = date.today()
        path = diary_path(settings.memory_dir, day)
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        return page(request, "diario.html", day=day.isoformat(), text=text)

    @app.post("/diario", dependencies=[Depends(check_csrf)])
    def write(request: Request, fecha: str = Form("")) -> HTMLResponse:
        day = date.fromisoformat(fecha) if fecha else date.today()
        try:
            written = brain.thinker and write_diary(
                brain.thinker, brain.store, settings.memory_dir, day
            )
        except OllamaError as exc:
            return HTMLResponse(f"<p>No pude escribirlo: {exc}</p>", status_code=503)
        text = written.read_text(encoding="utf-8") if written else "No hay conversaciones ese día."
        return page(request, "diario_texto.html", text=text)

    @app.get("/preparando", response_class=HTMLResponse)
    def preparing() -> HTMLResponse:
        """Sin red, avisa que el modelo local se está preparando; se consulta cada 2 s."""
        return HTMLResponse(preparing_html(brain))

    @app.get("/estado", response_class=HTMLResponse)
    def status(request: Request) -> HTMLResponse:
        layers = [
            {"name": layer.provider.name, "model": layer.provider.model, "state": state}
            for layer, state in brain.cascade.status()
        ]
        tools = brain.tools.status() if brain.tools is not None else []
        return page(
            request,
            "estado.html",
            layers=layers,
            tools=tools,
            pending=memoria.pending_list(),
            ntfy=ntfy_state(brain),
            ntfy_link=ntfy_link(settings),
            ntfy_server=settings.ntfy_public_url.strip().rstrip("/") if ntfy_link(settings) else "",
            ntfy_topic=settings.ntfy_topic.strip(),
            local=not remote(request),
            passkeys=access.passkeys(),
            voice=voice.status() if voice is not None else None,
            cloud_keys=cloud_keys_status(brain),
            claude_spend=brain.spend.status() if getattr(brain, "spend", None) else "",
        )

    @app.post("/estado/aviso-prueba", dependencies=[Depends(check_csrf)])
    def test_notice() -> HTMLResponse:
        return HTMLResponse(escape(commands.test_notice_text(brain)))

    if brain.warmer is not None:
        # La precarga usa el mismo prefijo que tendrá el próximo turno: sin red, la primera
        # respuesta no espera a que el modelo procese todo en frío (~1 min en CPU).
        brain.warmer.context = lambda: agent.prepare(
            brain.tools,
            history.trim(session.messages, session.max_tokens),
            session.max_tools,
            brain.focus,
        )
    return app


def preparing_html(brain: Brain) -> str:
    """El aviso de precarga: solo sin red, que es cuando la respuesta depende del modelo local."""
    warmer, connectivity = brain.warmer, brain.connectivity
    offline = connectivity is not None and not connectivity.online()
    if warmer is None or not offline or not warmer.busy:
        return '<div id="preparando"></div>'
    return (
        '<div id="preparando" class="preparando" hx-get="/preparando" hx-trigger="every 2s" '
        f'hx-swap="outerHTML">Sin internet: preparando el modelo local… ({warmer.seconds()} s). '
        "Puedes escribir; la respuesta llegará cuando esté listo.</div>"
    )


def cloud_keys_status(brain: Brain) -> list[str]:
    """Estado de las API keys de Ollama Cloud, por número (nunca la key)."""
    for layer in brain.cascade.layers:
        if hasattr(layer.provider, "keys_status"):
            return layer.provider.keys_status()
    return []


def ntfy_link(settings: Settings) -> str:
    """Enlace que abre la app ntfy del celular ya suscrita al tópico, en el servidor propio."""
    public = settings.ntfy_public_url.strip().rstrip("/")
    topic = settings.ntfy_topic.strip()
    if not public.startswith("https://") or not topic:
        return ""
    return f"ntfy://{public.removeprefix('https://')}/{quote(topic)}"


def ntfy_state(brain: Brain) -> str:
    engine = brain.proactive
    if engine is None or not engine.ntfy_url or not engine.ntfy_topic:
        return "sin configurar (TAMA_NTFY_URL y TAMA_NTFY_TOPIC)"
    if engine.ntfy_error:
        return f"falló el último envío: {engine.ntfy_error}"
    return f"activos, a {engine.ntfy_url}"


def is_remote(request: Request) -> bool:
    """Llega por `tailscale serve` (trae su cabecera) o con un nombre que no es el del PC."""
    host = request.headers.get("host", "").rsplit(":", 1)[0].lower()
    return bool(request.headers.get(TAILSCALE_USER)) or host not in LOCAL_HOSTS


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def rejection(request: Request, settings: Settings) -> tuple[str, str] | None:
    """Por qué se rechaza una petición: (mensaje para quien pregunta, detalle para la consola).

    En el PC vale localhost. Desde el celular, solo el nombre del PC en Tailscale, por
    `tailscale serve` (que agrega la cuenta de quien abre Tama) y, si se configuró, de esa cuenta.
    El mensaje dice la causa sin mostrar lo configurado.
    """
    host = request.headers.get("host", "").rsplit(":", 1)[0].rstrip(".").lower()
    user = request.headers.get(TAILSCALE_USER, "").strip().lower()
    expected = settings.web_tailscale_host.strip().rstrip(".").lower()
    allowed = settings.web_tailscale_user.strip().lower()
    if host not in LOCAL_HOSTS:
        if not user:
            return (
                "Tama solo acepta conexiones desde el PC o por tailscale serve.",
                f"host {host!r} sin la cabecera de tailscale serve",
            )
        if not expected:
            return (
                "Falta TAMA_WEB_TAILSCALE_HOST en el .env de Tama (y reiniciarla).",
                f"llegó por Tailscale a {host!r}, pero TAMA_WEB_TAILSCALE_HOST está vacío",
            )
        if host != expected:
            return (
                f"El nombre {host} no coincide con TAMA_WEB_TAILSCALE_HOST en el .env de Tama.",
                f"host {host!r}; TAMA_WEB_TAILSCALE_HOST={expected!r}",
            )
    if user and allowed and user != allowed:
        return (
            "La cuenta de Tailscale de este dispositivo no es la de TAMA_WEB_TAILSCALE_USER.",
            f"cuenta {user!r}; TAMA_WEB_TAILSCALE_USER={allowed!r}",
        )
    return None


STARTING = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="2"><title>Tama</title></head>
<body style="font-family: system-ui; padding: 2rem">{message}</body></html>"""


class Starting:
    """Abre el puerto de inmediato; lo lento (herramientas, red, modelo) se prepara detrás."""

    def __init__(self, build: Callable[[], FastAPI]) -> None:
        self.app: FastAPI | None = None
        self.problem = ""
        self.thread = threading.Thread(target=self._prepare, args=(build,), daemon=True)
        self.thread.start()

    def _prepare(self, build: Callable[[], FastAPI]) -> None:
        try:
            self.app = build()
        except Exception as exc:
            self.problem = str(exc)
            print(f"Tama no pudo iniciar: {exc}", file=sys.stderr, flush=True)

    async def __call__(self, scope, receive, send) -> None:
        if self.app is not None:
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            return
        message = (
            f"No pude iniciar: {escape(self.problem)}" if self.problem else "Tama está iniciando…"
        )
        body = STARTING.format(message=message).encode("utf-8")
        headers = [(b"content-type", b"text/html; charset=utf-8")]
        await send({"type": "http.response.start", "status": 503, "headers": headers})
        await send({"type": "http.response.body", "body": body})


def serve(settings: Settings) -> int:
    """`tama web`: abre el puerto y prepara Tama detrás; por defecto, solo en esta máquina."""
    import uvicorn

    prepared: list[Brain] = []

    def build() -> FastAPI:
        brain = brain_module.build(settings)
        prepared.append(brain)
        app = create_app(brain, settings)
        if brain.reflector is not None:
            # El diario pendiente se escribe en la primera inactividad, no al abrir: así no le
            # quita el modelo local a la primera respuesta.
            brain.reflector.start()
        if settings.local_preload:
            brain.preload()
        voice = app.state.voice
        if voice is not None:
            threading.Thread(target=voice.warm, name="tama-voz", daemon=True).start()
        if settings.connectivity_watch_s > 0:
            brain.watch_network(settings.connectivity_watch_s, settings.local_keep_warm)
        if brain.proactive is not None:
            brain.proactive.start()
        for layer in brain.cascade.layers:
            if hasattr(layer.provider, "startup_line"):
                print(f"Ollama Cloud: {layer.provider.startup_line()}", flush=True)
        print("Tama lista.", flush=True)
        return app

    try:
        uvicorn.run(
            Starting(build),
            host=settings.web_host,
            port=settings.web_port,
            log_level="warning",
            lifespan="off",
        )
    finally:
        for brain in prepared:
            brain.close()
    return 0
