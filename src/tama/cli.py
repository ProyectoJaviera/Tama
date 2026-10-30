"""CLI: `tama` abre el chat; `tama "mensaje"` responde una vez."""

import argparse
import os
import sys

from tama import brain as brain_module
from tama import history, prompts
from tama.brain import Brain
from tama.cascade import Answered, Switch
from tama.config import Settings, get_settings
from tama.ollama import OllamaClient, OllamaError
from tama.providers import Error, Message, Text, ToolCall

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


def reply(brain: Brain, messages: list[Message], max_tokens: int) -> str | None:
    """Responde un turno en streaming, lo registra en events.db y devuelve el texto (o None)."""
    parts: list[str] = []
    for event in brain.cascade.turn(history.trim(messages, max_tokens)):
        if isinstance(event, Text):
            print(event.text, end="", flush=True)
            parts.append(event.text)
        elif isinstance(event, ToolCall):
            pass  # las herramientas llegan en el Sprint 2
        elif isinstance(event, Switch):
            if event.partial:
                print()
            parts.clear()  # la siguiente capa repite el turno completo
            print(dim(f"· {event.frm} no respondió ({event.reason}); seguí en {event.to}"))
            brain.store.log(
                "provider_switch", f"{event.frm} → {event.to}: {event.reason}", event.to
            )
        elif isinstance(event, Answered):
            text = "".join(parts)
            print()
            ttft = f"{event.ttft_s:.1f} s" if event.ttft_s is not None else "—"
            where = f"· {event.provider} ({event.model})"
            speed = f" · {event.tokens_per_s:.0f} tok/s" if event.tokens_per_s else ""
            print(dim(f"{where} · primer token {ttft} · total {event.total_s:.1f} s{speed}"))
            brain.store.log(
                "assistant_msg",
                text,
                event.provider,
                event.model,
                event.input_tokens,
                event.output_tokens,
                round(event.ttft_s * 1000) if event.ttft_s is not None else None,
                round(event.total_s * 1000),
            )
            return text
        elif isinstance(event, Error):
            print(f"\nNo pude responder: {event.message}", file=sys.stderr)
            brain.store.log("error", event.message)
            return None
    return None


def turn(brain: Brain, messages: list[Message], text: str, max_tokens: int) -> bool:
    messages.append({"role": "user", "content": text})
    brain.store.log("user_msg", text)
    answer = reply(brain, messages, max_tokens)
    if answer is None:
        messages.pop()
        return False
    messages.append({"role": "assistant", "content": answer})
    return True


def chat_loop(brain: Brain, messages: list[Message], max_tokens: int) -> None:
    print("Tama. Escribe /proveedor para ver las capas o /salir para terminar.")
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
        if text:
            turn(brain, messages, text, max_tokens)


def main(
    argv: list[str] | None = None, brain: Brain | None = None, settings: Settings | None = None
) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    settings = settings or get_settings()
    brain = brain or brain_module.build(settings)
    messages: list[Message] = [{"role": "system", "content": prompts.identity()}]
    try:
        problem = health_check(brain.local, settings.ollama_host, settings.local_model)
        if args.check:
            print_status(brain)
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
                if turn(brain, messages, " ".join(args.mensaje), settings.history_max_tokens)
                else 1
            )
        chat_loop(brain, messages, settings.history_max_tokens)
        return 0
    except KeyboardInterrupt:
        print()
        return 130
    finally:
        brain.close()
