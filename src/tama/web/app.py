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
from collections.abc import Callable, Iterator
from datetime import date
from html import escape
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from tama import agent, chat, commands
from tama import brain as brain_module
from tama.brain import Brain
from tama.cascade import Answered, Switch
from tama.config import Settings
from tama.mcp_servers import memoria
from tama.memory import MAX_PROFILE_CHARS, session_prompt
from tama.ollama import OllamaError
from tama.proactive import act_on
from tama.providers import Error, Message, Text, ToolCall
from tama.reflection import diary_path, write_diary

HERE = Path(__file__).parent
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
END = object()


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


def stream_turn(brain: Brain, session: "Session", turn: Turn, text: str) -> None:
    answered: Answered | None = None
    try:
        for event in chat.run_turn(
            brain, session.messages, text, session.max_tokens, session.max_tools, turn.confirm
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
    finally:
        turn.events.put(END)
        session.finish()


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
        self._busy = threading.Lock()

    def start(self, text: str) -> str | None:
        """Lanza un turno; devuelve su id, o None si ya hay uno en curso."""
        if not self._busy.acquire(blocking=False):
            return None
        if self.brain.reflector is not None:
            self.brain.reflector.activity()
        turn_id = secrets.token_hex(8)
        turn = self.turns[turn_id] = Turn(self.timeout_s)
        threading.Thread(
            target=stream_turn, args=(self.brain, self, turn, text), daemon=True
        ).start()
        return turn_id

    def finish(self) -> None:
        if self.brain.reflector is not None:
            self.brain.reflector.activity()
        if self.brain.proactive is not None:
            self.brain.proactive.activity()
        self._busy.release()


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
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.middleware("http")
    async def only_localhost(request: Request, call_next):
        # Evita que un sitio externo llegue al servidor con un dominio propio (DNS rebinding).
        host = request.headers.get("host", "").rsplit(":", 1)[0]
        if host not in LOCAL_HOSTS:
            return PlainTextResponse("Solo localhost", status_code=403)
        return await call_next(request)

    def check_csrf(request: Request) -> None:
        if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), csrf):
            raise HTTPException(status_code=403, detail="Falta el token CSRF")

    def page(request: Request, name: str, **context: Any) -> HTMLResponse:
        context = {"csrf": csrf, "v": STATIC_VERSION, **context}
        return templates.TemplateResponse(request, name, context)

    def chat_history() -> list[tuple[str, str]]:
        """La conversación, con los comandos y los avisos, tal como se vio."""
        types = ("user_msg", "assistant_msg", "command", "proactive")
        shown: list[tuple[str, str]] = []
        for kind, content in brain.store.recent_messages(40, types):
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
    def send(text: str = Form(...)) -> dict[str, Any]:
        text = text.strip()
        if not text:
            raise HTTPException(status_code=422, detail="Mensaje vacío")
        if text.startswith("/"):
            command = commands.canonical(text)
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
        turn_id = session.start(text)
        if turn_id is None:
            raise HTTPException(status_code=409, detail="Tama aún está respondiendo")
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

    @app.post("/memoria/perfil", dependencies=[Depends(check_csrf)])
    def save_profile(request: Request, texto: str = Form("")) -> HTMLResponse:
        if len(texto.strip()) > MAX_PROFILE_CHARS:
            raise HTTPException(status_code=422, detail="El perfil es demasiado largo")
        profile_path().parent.mkdir(parents=True, exist_ok=True)
        profile_path().write_text(texto.strip() + "\n", encoding="utf-8")
        return HTMLResponse("<p class='ok'>Perfil guardado.</p>")

    def show_card(request: Request, entity_id: int) -> HTMLResponse:
        found = memoria.card(entity_id)
        if found is None:
            return HTMLResponse("<p>Esa ficha ya no existe.</p>")
        others = [c for c in memoria.list_cards() if c["id"] != entity_id]
        return page(request, "ficha.html", card=found, others=others)

    @app.get("/memoria/ficha/{entity_id}", response_class=HTMLResponse)
    def card_detail(request: Request, entity_id: int) -> HTMLResponse:
        return show_card(request, entity_id)

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
        brain.proactive.busy = lambda: session._busy.locked()

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

    @app.get("/estado", response_class=HTMLResponse)
    def status(request: Request) -> HTMLResponse:
        layers = [
            {"name": layer.provider.name, "model": layer.provider.model, "state": state}
            for layer, state in brain.cascade.status()
        ]
        tools = brain.tools.status() if brain.tools is not None else []
        return page(
            request, "estado.html", layers=layers, tools=tools, pending=memoria.pending_list()
        )

    return app


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
            brain.reflector.close_days()
            brain.reflector.start()
        if brain.proactive is not None:
            brain.proactive.start()
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
