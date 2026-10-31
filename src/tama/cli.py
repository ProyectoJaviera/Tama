"""CLI: `tama` abre el chat; `tama "mensaje"` responde una vez."""

import argparse
import json
import os
import re
import sys

from tama import agent, history
from tama import brain as brain_module
from tama.brain import Brain
from tama.cascade import Answered, Switch
from tama.config import Settings, get_settings
from tama.memory import session_prompt
from tama.ollama import OllamaClient, OllamaError
from tama.providers import Error, Message, Text, ToolCall

EXIT_COMMANDS = {"/salir", "salir", "exit", "quit"}
# La consola de Windows muestra los emojis como cuadros.
UNPRINTABLE = re.compile("[\U0001f000-\U0001faff\u2600-\u27bf\ufe0f\u200d]")


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


def printable(text: str) -> str:
    return UNPRINTABLE.sub("", text) if sys.platform == "win32" else text


def confirm_question(call: ToolCall) -> str:
    """La pregunta en palabras de Renzo, sin el JSON de la herramienta."""
    args = call.arguments
    if call.name == "delete_observations":
        items = [o for d in args.get("deletions", []) for o in d.get("observations", [])]
        return "¿Borro de la memoria: " + ", ".join(f"«{short(i)}»" for i in items) + "?"
    if call.name == "delete_note":
        return f"¿Borro la nota {args.get('note_id')}?"
    if call.name == "update_note":
        return f"¿Cambio la nota {args.get('note_id')}?"
    if call.name == "overwrite_file":
        return f"¿Reemplazo todo el contenido de {args.get('path')}?"
    if call.name == "proponer_cambio_perfil":
        return f"¿Agrego a tu perfil: «{short(args.get('cambio', ''))}»?"
    return f"¿Confirmas {call.name}?"


def ask_yes(question: str) -> bool:
    try:
        return input(f"{question} (s/n) ").strip().lower() in {"s", "si", "sí"}
    except EOFError:
        return False


def confirm(call: ToolCall, info) -> bool:
    """Pide a Renzo un "s" explícito antes de una acción destructiva."""
    return ask_yes(confirm_question(call))


def short(value: object, limit: int = 60) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def reply(
    brain: Brain, messages: list[Message], max_tokens: int, max_tools: int = 10
) -> agent.FinalAnswer | None:
    """Responde un turno en streaming (con herramientas), lo registra y devuelve el cierre."""
    answered: Answered | None = None
    for event in agent.run_turn(
        brain.cascade, brain.tools, history.trim(messages, max_tokens), confirm, max_tools
    ):
        if isinstance(event, Text):
            print(printable(event.text), end="", flush=True)
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
                if answered.skipped:
                    skipped = "; ".join(answered.skipped)
                    print(dim(f"· sin usar: {skipped}"))
                    brain.store.log("provider_switch", skipped, answered.provider)
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


def show_memory(brain: Brain, args: str = "") -> None:
    """`/memoria` muestra lo vigente; `/memoria historial <tema>`, la línea de tiempo."""
    if brain.tools is None:
        print(dim("  sin memoria: los servidores MCP no están activos"))
        return
    if args.startswith("historial"):
        tema = args[len("historial") :].strip()
        print(dim(brain.tools.call("linea_de_tiempo", {"tema": tema}).text))
        return
    print(dim(brain.tools.call("search_nodes", {"query": ""}).text))


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


def export_memory(settings: Settings) -> int:
    """`tama memoria export`: vuelca el grafo a memoria/grafo.jsonl."""
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
    print("Tama. Comandos: /proveedor, /herramientas, /memoria, /olvidar <tema> y /salir.")
    if brain.warmer is not None:
        # La precarga debe usar el mismo prefijo que el próximo turno: conversación y herramientas.
        brain.warmer.context = lambda: agent.prepare(
            brain.tools, history.trim(messages, max_tokens), max_tools
        )
    if watch_s > 0:
        brain.watch_network(watch_s, keep_warm)
    while True:
        try:
            text = input("> ").strip()
        except EOFError:
            print()
            return
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
        if text:
            turn(brain, messages, text, max_tokens, max_tools)


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
    system = session_prompt(settings.memory_dir, brain.store)
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
