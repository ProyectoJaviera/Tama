"""CLI: `tama` abre el chat; `tama "mensaje"` responde una vez."""

import argparse
import os
import sys
import threading
from datetime import date

from tama import agent, history
from tama import brain as brain_module
from tama.brain import Brain
from tama.cascade import Answered, Switch
from tama.config import Settings, get_settings
from tama.mcp_servers.memoria import NO_SUGGESTION
from tama.memory import session_prompt
from tama.ollama import OllamaClient, OllamaError
from tama.providers import Error, Message, Text, ToolCall
from tama.reflection import diary_path, write_diary

EXIT_COMMANDS = {"/salir", "salir", "exit", "quit"}


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


def confirm(call: ToolCall, info) -> bool:
    """Pide a Renzo un "s" explícito antes de una acción destructiva."""
    args = ", ".join(f"{k}={v!r}" for k, v in call.arguments.items())
    try:
        answer = input(f"¿Confirmas {info.server}.{call.name}({args})? (s/n) ")
    except EOFError:
        return False
    return answer.strip().lower() in {"s", "si", "sí"}


def short(value: object, limit: int = 60) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def reply(
    brain: Brain, messages: list[Message], max_tokens: int, max_tools: int = 10
) -> agent.FinalAnswer | None:
    """Responde un turno en streaming (con herramientas), lo registra y devuelve el cierre."""
    answered: Answered | None = None
    for event in agent.run_turn(
        brain.cascade,
        brain.tools,
        history.trim(messages, max_tokens),
        confirm,
        max_tools,
        focus=brain.focus,
    ):
        if isinstance(event, Text):
            print(event.text, end="", flush=True)
        elif isinstance(event, Switch):
            if event.partial:
                print()
            print(dim(f"· {event.frm} no respondió ({event.reason}); seguí en {event.to}"))
            brain.store.log(
                "provider_switch", f"{event.frm} → {event.to}: {event.reason}", event.to
            )
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
                ttft = f"{answered.ttft_s:.1f} s" if answered.ttft_s is not None else "-"
                where = f"· {answered.provider} ({answered.model})"
                speed = f" · {answered.tokens_per_s:.0f} tok/s" if answered.tokens_per_s else ""
                print(dim(f"{where} · primer token {ttft} · total {answered.total_s:.1f} s{speed}"))
                brain.store.log(
                    "assistant_msg",
                    event.text,
                    answered.provider,
                    answered.model,
                    answered.input_tokens,
                    answered.output_tokens,
                    round(answered.ttft_s * 1000) if answered.ttft_s is not None else None,
                    round(answered.total_s * 1000),
                )
            return event
        elif isinstance(event, Error):
            print(f"\nNo pude responder: {event.message}", file=sys.stderr)
            brain.store.log("error", event.message)
            return None
    return None


def show_memory(brain: Brain) -> None:
    if brain.tools is None:
        print(dim("  sin memoria: los servidores MCP no están activos"))
        return
    print(dim(brain.tools.call("search_nodes", {"query": ""}).text))


def forget(brain: Brain, entity: str) -> None:
    """Borra una entidad de la memoria, siempre con confirmación."""
    if brain.tools is None or not entity:
        print(dim("  uso: /olvidar <entidad>"))
        return
    try:
        answer = input(f"¿Olvido todo lo que sé sobre {entity}? (s/n) ")
    except EOFError:
        answer = "n"
    if answer.strip().lower() not in {"s", "si", "sí"}:
        print(dim("  no se borró nada"))
        return
    print(dim(brain.tools.call("delete_entities", {"entityNames": [entity]}).text))


def ask_yes(question: str) -> bool:
    try:
        return input(f"{question} (s/n) ").strip().lower() in {"s", "si", "sí"}
    except EOFError:
        return False


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
    """`tama memoria export`: vuelca el grafo y los objetivos a memoria/grafo.jsonl."""
    os.environ["TAMA_DATA_DIR"] = str(settings.data_dir)
    from tama.mcp_servers import memoria

    target = settings.memory_dir / "grafo.jsonl"
    count = memoria.export_graph(target)
    print(f"{count} entidades exportadas a {target}")
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
    messages.append({"role": "user", "content": text})
    event_id = brain.store.log("user_msg", text)
    if brain.tools is not None:
        brain.tools.current_event = event_id
    final = reply(brain, messages, max_tokens, max_tools)
    if final is None:
        messages.pop()
        return False
    messages.extend(final.trace)
    messages.append({"role": "assistant", "content": final.text})
    return True


def chat_loop(
    brain: Brain,
    messages: list[Message],
    max_tokens: int,
    watch_s: float = 0.0,
    keep_warm: bool = False,
    max_tools: int = 10,
) -> None:
    print(
        "Tama. Comandos: /proveedor, /herramientas, /memoria, /olvidar <entidad>, /objetivos, "
        "/diario [fecha] y /salir."
    )
    if brain.reflector is not None:
        brain.reflector.close_days()
        brain.reflector.start()
    if brain.warmer is not None:
        # La precarga debe usar el mismo prefijo que el próximo turno: conversación y herramientas.
        brain.warmer.context = lambda: agent.prepare(
            brain.tools, history.trim(messages, max_tokens), max_tools, brain.focus
        )
    if watch_s > 0:
        brain.watch_network(watch_s, keep_warm)
    while True:
        try:
            text = input("> ").strip()
        except EOFError:
            print()
            return
        if brain.reflector is not None:
            brain.reflector.activity()
        if text.lower() in EXIT_COMMANDS:
            return
        if text.lower() == "/proveedor":
            print_status(brain)
            continue
        if text.lower() == "/herramientas":
            print_tools(brain)
            continue
        if text.lower() == "/memoria":
            show_memory(brain)
            continue
        if text.lower().startswith("/olvidar"):
            forget(brain, text[len("/olvidar") :].strip())
            continue
        if text.lower().startswith("/objetivos"):
            show_goals(brain, text[len("/objetivos") :].strip())
            continue
        if text.lower().startswith("/diario"):
            show_diary(brain, text[len("/diario") :].strip())
            continue
        if text:
            turn(brain, messages, text, max_tokens, max_tools)
        if brain.reflector is not None:
            brain.reflector.activity()


def main(
    argv: list[str] | None = None, brain: Brain | None = None, settings: Settings | None = None
) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    settings = settings or get_settings()
    if args.mensaje[:2] == ["memoria", "export"]:
        return export_memory(settings)
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
