"""CLI: `tama` abre el chat; `tama "mensaje"` responde una vez."""

import argparse
import json
import os
import sys
import threading
from datetime import date

from tama import agent, chat, commands, history
from tama import brain as brain_module
from tama.brain import Brain
from tama.cascade import Answered, Switch
from tama.chat import confirm_question, short
from tama.config import Settings, get_settings
from tama.mcp_servers import memoria
from tama.mcp_servers.memoria import NO_SUGGESTION
from tama.memory import session_prompt
from tama.ollama import OllamaClient, OllamaError
from tama.providers import Error, Message, Text, ToolCall
from tama.reflection import diary_path, write_diary

EXIT_COMMANDS = {"/salir", "salir", "exit", "quit"}
COMMANDS_ONLY = {"/recordatorios", "/avisos", "/silencio", "/resumen", "/ayuda", "/estado"}


def cancel(args: str) -> None:
    """`/cancelar <n>`: muestra el recordatorio y lo cancela solo con un sí."""
    preview, number = commands.cancel_preview(args)
    print(dim(f"  {preview}"))
    if number is None:
        return
    if not ask_yes("¿Lo cancelo?"):
        print(dim("  no se canceló"))
        return
    print(dim(f"  {memoria.cancel_reminder(number)}"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tama", description="Conversa con Tama.")
    parser.add_argument("mensaje", nargs="*", help="mensaje a enviar; sin mensaje abre un chat")
    parser.add_argument(
        "--check", action="store_true", help="comprueba Ollama local y el estado de las capas"
    )
    return parser


def dim(text: str) -> str:
    """Texto en gris, solo en una terminal con colores."""
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return text
    return f"\x1b[90m{text}\x1b[0m"


def health_check(client: OllamaClient, host: str, model: str) -> str | None:
    """Devuelve None si Ollama local responde y tiene el modelo; si no, el problema en español."""
    try:
        models = client.list_models()
    except OllamaError:
        return (
            f"Ollama no está corriendo en el host ({host}). "
            "Inícialo con `ollama serve` o abre la app de Ollama."
        )
    if model not in models:
        return f"El modelo {model} no está descargado. Instálalo una vez con `ollama pull {model}`."
    return None


def print_status(brain: Brain) -> None:
    for layer, state in brain.cascade.status():
        print(dim(f"  {layer.provider.name:<7} {layer.provider.model:<32} {state}"))


def ask_yes(question: str) -> bool:
    try:
        return input(f"{question} (s/n) ").strip().lower() in {"s", "si", "sí"}
    except EOFError:
        return False


def confirm(call: ToolCall, info) -> bool:
    """Pide a Renzo un "s" explícito antes de una acción destructiva."""
    return ask_yes(confirm_question(call))


def reply(
    brain: Brain, messages: list[Message], text: str, max_tokens: int, max_tools: int = 10
) -> agent.FinalAnswer | None:
    """Muestra un turno en streaming (con herramientas) y devuelve el cierre."""
    answered: Answered | None = None
    for event in chat.run_turn(brain, messages, text, max_tokens, max_tools, confirm):
        if isinstance(event, Text):
            print(chat.without_emojis(event.text), end="", flush=True)
        elif isinstance(event, Switch):
            if event.partial:
                print()
            print(dim(f"· {event.frm} no respondió ({event.reason}); seguí en {event.to}"))
        elif isinstance(event, agent.ToolUse):
            args = ", ".join(f"{k}={short(v, 30)}" for k, v in event.arguments.items())
            print(dim(f"· {event.server or '?'}.{event.name}({args})"))
        elif isinstance(event, agent.ToolResult):
            if event.skipped:
                print(dim("  -> no se ejecutó (sin confirmación)"))
            elif not event.ok:
                print(dim(f"  -> error: {short(event.text)}"))
        elif isinstance(event, Answered):
            answered = event
        elif isinstance(event, agent.FinalAnswer):
            print()
            if answered is not None:
                print(dim(f"· {chat.speed_line(answered)}"))
                if answered.skipped:
                    print(dim(f"· sin usar: {'; '.join(answered.skipped)}"))
            return event
        elif isinstance(event, Error):
            print(f"\nNo pude responder: {event.message}", file=sys.stderr)
            return None
    return None


def show_memory(brain: Brain, args: str = "") -> None:
    print(dim(commands.memory_text(brain, args)))


def choose(items: list[dict]) -> list[dict]:
    """Deja elegir qué olvidar: un número, "todas" o nada."""
    if len(items) == 1:
        return items
    try:
        answer = input("¿Cuál olvido? (número, todas, o Enter para cancelar) ").strip().lower()
    except EOFError:
        return []
    if answer == "todas":
        return items
    return [items[int(answer) - 1]] if answer.isdigit() and 0 < int(answer) <= len(items) else []


def forget(brain: Brain, query: str) -> None:
    """Busca por palabras qué recuerda Tama y borra lo elegido, siempre con confirmación."""
    if brain.tools is None or not query:
        print(dim("  uso: /olvidar <tema>"))
        return
    outcome = brain.tools.call("find_memory", {"query": query})
    if not outcome.ok:
        print(dim(f"  {short(outcome.text)}"))
        return
    found = json.loads(outcome.text)
    items, messages = found["items"], found["mensajes"]
    if not items and not messages:
        similar = ", ".join(found["parecidas"]) or "ninguna"
        print(dim(f"  no encontré nada sobre eso. Entidades parecidas: {similar}"))
        return
    for number, item in enumerate(items, 1):
        print(dim(f"  {number}. {item['texto']}"))
    if messages:
        print(dim(f"  además, {messages} mensajes de conversación lo mencionan"))
    chosen = choose(items) if items else []
    if (items and not chosen) or not ask_yes("¿Lo olvido para siempre?"):
        print(dim("  no se borró nada"))
        return
    arguments = {
        "entidades": [i["entidad"] for i in chosen if "entidad" in i],
        "datos": [i["dato"] for i in chosen if "dato" in i],
        "consulta": query,
    }
    print(dim(brain.tools.call("forget_memory", arguments).text))


def show_goals(brain: Brain, args: str) -> None:
    """`/objetivos` lista todos; `/objetivos borrar <objetivo>` borra uno, con confirmación."""
    if brain.tools is None:
        print(dim("  sin memoria: los servidores MCP no están activos"))
        return
    if not args.startswith("borrar"):
        print(dim(brain.tools.call("list_goals", {"estado": "todos"}).text))
        return
    goal = args[len("borrar") :].strip()
    if not goal or not ask_yes(f"¿Borro el objetivo {goal}?"):
        print(dim("  no se borró nada"))
        return
    print(dim(brain.tools.call("delete_goal", {"objetivo": goal}).text))


def show_diary(brain: Brain, args: str) -> None:
    """`/diario` escribe el de hoy; `/diario AAAA-MM-DD` muestra (o escribe) el de esa fecha."""
    today = date.today()
    try:
        day = date.fromisoformat(args) if args else today
    except ValueError:
        print(dim("  uso: /diario [AAAA-MM-DD]"))
        return
    path = diary_path(brain.memory_dir, day)
    if day == today or not path.exists():
        if brain.thinker is None:
            print(dim("  sin diario: la memoria no está activa"))
            return
        print(dim("· escribiendo el diario con el modelo local..."))
        try:
            brain.thinker.cancel = threading.Event()
            written = write_diary(brain.thinker, brain.store, brain.memory_dir, day)
        except OllamaError as exc:
            print(f"No pude escribir el diario: {exc}", file=sys.stderr)
            return
        if written is None:
            print(dim(f"  no hay conversaciones del {day.isoformat()}"))
            return
    print(path.read_text(encoding="utf-8"))


def pending_suggestion(brain: Brain) -> str:
    if brain.tools is None:
        return ""
    outcome = brain.tools.call("next_suggestion", {})
    return outcome.text if outcome.ok and outcome.text != NO_SUGGESTION else ""


def export_memory(settings: Settings) -> int:
    """`tama memoria export`: vuelca el grafo a memoria/grafo.jsonl."""
    os.environ["TAMA_DATA_DIR"] = str(settings.data_dir)
    from tama.mcp_servers import memoria

    target = settings.memory_dir / "grafo.jsonl"
    count = memoria.export_graph(target)
    print(f"{count} entidades exportadas a {target}")
    return 0


def repair_memory(settings: Settings) -> int:
    """`tama memoria reparar`: ordena las cadenas de reemplazos de versiones anteriores."""
    os.environ["TAMA_DATA_DIR"] = str(settings.data_dir)
    from tama.mcp_servers import memoria

    print(f"{memoria.repair_chains()} enlaces reparados")
    return 0


def print_tools(brain: Brain) -> None:
    if brain.tools is None:
        print(dim("  sin herramientas configuradas"))
        return
    for name, state, tools in brain.tools.status():
        print(dim(f"  {name:<9} {state:<14} {', '.join(tools)}"))


def turn(
    brain: Brain, messages: list[Message], text: str, max_tokens: int, max_tools: int = 10
) -> bool:
    return reply(brain, messages, text, max_tokens, max_tools) is not None


def chat_loop(
    brain: Brain,
    messages: list[Message],
    max_tokens: int,
    watch_s: float = 0.0,
    keep_warm: bool = False,
    max_tools: int = 10,
) -> None:
    print(
        "Tama. Comandos: /proveedor, /herramientas, /memoria, /olvidar <tema>, /objetivos, "
        "/diario [fecha] y /salir."
    )
    if brain.reflector is not None:
        brain.reflector.close_days()
        brain.reflector.start()
    inbox: list[dict] = []
    if brain.proactive is not None:
        brain.proactive.listeners.append(inbox.append)
        brain.proactive.start()
    if brain.warmer is not None:
        # La precarga debe usar el mismo prefijo que el próximo turno: conversación y herramientas.
        brain.warmer.context = lambda: agent.prepare(
            brain.tools, history.trim(messages, max_tokens), max_tools, brain.focus
        )
    if watch_s > 0:
        brain.watch_network(watch_s, keep_warm)
    while True:
        # Entre turnos: nunca se interrumpe a Renzo mientras escribe.
        while inbox:
            print(dim(f"· Aviso: {inbox.pop(0)['text']}"))
        try:
            text = input("> ").strip()
        except EOFError:
            print()
            return
        if brain.reflector is not None:
            brain.reflector.activity()
        if brain.proactive is not None:
            brain.proactive.activity()
        if text.startswith("/"):
            text = commands.canonical(text)
        if text.lower() in EXIT_COMMANDS:
            return
        if text.lower() == "/proveedor":
            print_status(brain)
            continue
        if text.lower() == "/herramientas":
            print_tools(brain)
            continue
        if text.lower().startswith("/memoria"):
            show_memory(brain, text[len("/memoria") :].strip())
            continue
        if text.lower().startswith("/olvidar"):
            forget(brain, text[len("/olvidar") :].strip())
            continue
        if text.startswith("/cancelar"):
            cancel(text[len("/cancelar") :].strip())
            continue
        if text.lower().startswith("/objetivos"):
            show_goals(brain, text[len("/objetivos") :].strip())
            continue
        if text.lower().startswith("/diario"):
            show_diary(brain, text[len("/diario") :].strip())
            continue
        if text.lower().split(" ")[0] in COMMANDS_ONLY:
            print(dim(commands.run(brain, text, brain.memory_dir)))
            continue
        if text:
            turn(brain, messages, text, max_tokens, max_tools)
        if brain.reflector is not None:
            brain.reflector.activity()
        if brain.proactive is not None:
            brain.proactive.activity()


def main(
    argv: list[str] | None = None, brain: Brain | None = None, settings: Settings | None = None
) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    settings = settings or get_settings()
    if args.mensaje[:2] == ["memoria", "export"]:
        return export_memory(settings)
    if args.mensaje[:2] == ["memoria", "reparar"]:
        return repair_memory(settings)
    if args.mensaje == ["web"]:
        # Antes de importar el servidor: esa carga ya toma unos segundos.
        print(f"Tama web en http://{settings.web_host}:{settings.web_port}", flush=True)
        from tama.web.app import serve

        return serve(settings)
    brain = brain or brain_module.build(settings)
    suggestion = "" if args.mensaje or args.check else pending_suggestion(brain)
    system = session_prompt(settings.memory_dir, brain.store, suggestion)
    messages: list[Message] = [{"role": "system", "content": system}]
    try:
        problem = health_check(brain.local, settings.ollama_host, settings.local_model)
        if args.check:
            print_status(brain)
            print_tools(brain)
            if problem:
                print(problem, file=sys.stderr)
                return 1
            host, model = settings.ollama_host, settings.local_model
            print(f"OK: Ollama local responde en {host} y tiene {model}.")
            return 0
        if problem:
            # Sin la capa local Tama aún puede responder con la nube; solo se avisa.
            print(dim(f"· {problem}"), file=sys.stderr)
        if args.mensaje:
            return (
                0
                if turn(
                    brain,
                    messages,
                    " ".join(args.mensaje),
                    settings.history_max_tokens,
                    settings.tools_max,
                )
                else 1
            )
        chat_loop(
            brain,
            messages,
            settings.history_max_tokens,
            settings.connectivity_watch_s,
            settings.local_keep_warm,
            settings.tools_max,
        )
        return 0
    except KeyboardInterrupt:
        print()
        return 130
    finally:
        brain.close()
