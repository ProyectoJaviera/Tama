"""CLI mínima: `uv run tama "hola"` conversa con el modelo local en streaming."""

import argparse
import sys

from tama.config import get_settings
from tama.ollama import Message, OllamaClient, OllamaError

SYSTEM_PROMPT = (
    "Eres Tama, el asistente personal y acompañante diario de Renzo. "
    "Respondes en español, con calidez y de forma breve."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tama", description="Conversa con Tama.")
    parser.add_argument("mensaje", nargs="*", help="mensaje a enviar; sin mensaje abre un chat")
    parser.add_argument("--modelo", help="modelo de Ollama a usar (por defecto, el de .env)")
    parser.add_argument(
        "--check", action="store_true", help="solo comprueba que Ollama y el modelo respondan"
    )
    return parser


def health_check(client: OllamaClient, host: str, model: str) -> str | None:
    """Devuelve None si Ollama responde y tiene el modelo; si no, el problema en español."""
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


def reply(client: OllamaClient, model: str, messages: list[Message], think: bool) -> str:
    """Muestra la respuesta del modelo en streaming y devuelve el texto completo."""
    parts: list[str] = []
    for chunk in client.stream_chat(model, messages, think=think):
        print(chunk, end="", flush=True)
        parts.append(chunk)
    print()
    return "".join(parts)


def chat_loop(client: OllamaClient, model: str, messages: list[Message], think: bool) -> None:
    print(f"Tama ({model}). Escribe 'salir' para terminar.")
    while True:
        try:
            text = input("> ").strip()
        except EOFError:
            print()
            return
        if text.lower() in {"salir", "exit", "quit"}:
            return
        if not text:
            continue
        messages.append({"role": "user", "content": text})
        try:
            answer = reply(client, model, messages, think)
        except OllamaError as exc:
            messages.pop()
            print(f"\nNo pude hablar con Ollama: {exc}", file=sys.stderr)
            continue
        messages.append({"role": "assistant", "content": answer})


def main(argv: list[str] | None = None, client: OllamaClient | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    settings = get_settings()
    model = args.modelo or settings.local_model
    client = client or OllamaClient(settings.ollama_host, timeout=settings.timeout)
    messages: list[Message] = [{"role": "system", "content": SYSTEM_PROMPT}]

    try:
        problem = health_check(client, settings.ollama_host, model)
        if problem:
            print(problem, file=sys.stderr)
            # En el chat interactivo se avisa y se sigue: Ollama puede encenderse después.
            if args.check or args.mensaje:
                return 1
        if args.check:
            print(f"OK: Ollama responde en {settings.ollama_host} y tiene {model}.")
        elif args.mensaje:
            messages.append({"role": "user", "content": " ".join(args.mensaje)})
            reply(client, model, messages, settings.think)
        else:
            chat_loop(client, model, messages, settings.think)
        return 0
    except OllamaError as exc:
        print(f"\nError con Ollama: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
        return 130
    finally:
        client.close()
