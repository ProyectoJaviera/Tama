"""Motor de un turno de conversación, compartido por la CLI y la web.

Registra lo ocurrido en events.db y actualiza el historial; quien llama solo muestra los eventos.
"""

import re
from collections.abc import Iterator

from tama import agent, history
from tama.brain import Brain
from tama.cascade import Answered, Switch
from tama.mcp_servers import notas
from tama.providers import Error, Message, ToolCall

EMOJIS = re.compile("[\U0001f000-\U0001faff\u2600-\u27bf\ufe0f\u200d]")


def without_emojis(text: str) -> str:
    """El modelo local los usa aunque la identidad se lo prohíbe."""
    return EMOJIS.sub("", text)


def short(value: object, limit: int = 60) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def note_question(verb: str, note_id: object) -> str:
    """Muestra la nota real: su número no le dice nada a Renzo."""
    note = notas.title_of(note_id)
    if not note:
        return f"¿{verb} la nota {note_id}? No la encuentro."
    return f"¿{verb} la nota «{short(note, 80)}»?"


def confirm_question(call: ToolCall) -> str:
    """La pregunta en palabras de Renzo, sin el JSON de la herramienta."""
    args = call.arguments
    if call.name == "delete_observations":
        items = [o for d in args.get("deletions", []) for o in d.get("observations", [])]
        return "¿Borro de la memoria: " + ", ".join(f"«{short(i)}»" for i in items) + "?"
    if call.name == "delete_note":
        return note_question("Borro", args.get("note_id"))
    if call.name == "update_note":
        return note_question("Cambio", args.get("note_id"))
    if call.name == "overwrite_file":
        return f"¿Reemplazo todo el contenido de {args.get('path')}?"
    if call.name == "discard_goal":
        return f"¿Descarto el objetivo «{short(args.get('objetivo', ''))}»?"
    if call.name == "cancel_reminder":
        return f"¿Cancelo el recordatorio #{args.get('numero')}?"
    if call.name == "proponer_cambio_perfil":
        return f"¿Agrego a tu perfil: «{short(args.get('cambio', ''))}»?"
    return f"¿Confirmas {call.name}?"


def run_turn(
    brain: Brain,
    messages: list[Message],
    text: str,
    max_tokens: int,
    max_tools: int,
    confirm: agent.Confirm,
) -> Iterator[agent.AgentEvent]:
    """Corre un turno completo; si falla, el historial queda como estaba."""
    messages.append({"role": "user", "content": text})
    event_id = brain.store.log("user_msg", text)
    if brain.tools is not None:
        brain.tools.current_event = event_id
    answered: Answered | None = None
    for event in agent.run_turn(
        brain.cascade,
        brain.tools,
        history.trim(messages, max_tokens),
        confirm,
        max_tools,
        focus=brain.focus,
    ):
        if isinstance(event, Switch):
            brain.store.log(
                "provider_switch", f"{event.frm} → {event.to}: {event.reason}", event.to
            )
        elif isinstance(event, Answered):
            answered = event
        elif isinstance(event, agent.ToolResult) and not event.ok and not event.skipped:
            brain.store.log("error", f"{event.name}: {short(event.text, 300)}")
        elif isinstance(event, Error):
            brain.store.log("error", event.message)
            messages.pop()
        elif isinstance(event, agent.FinalAnswer):
            if answered is not None:
                _log_answer(brain, answered, event.text)
            messages.extend(event.trace)
            messages.append({"role": "assistant", "content": event.text})
        yield event


def _log_answer(brain: Brain, answered: Answered, text: str) -> None:
    if answered.skipped:
        brain.store.log("provider_switch", "; ".join(answered.skipped), answered.provider)
    brain.store.log(
        "assistant_msg",
        text,
        answered.provider,
        answered.model,
        answered.input_tokens,
        answered.output_tokens,
        round(answered.ttft_s * 1000) if answered.ttft_s is not None else None,
        round(answered.total_s * 1000),
    )


def speed_line(answered: Answered) -> str:
    """La línea gris bajo cada respuesta."""
    ttft = f"{answered.ttft_s:.1f} s" if answered.ttft_s is not None else "-"
    speed = f" · {answered.tokens_per_s:.0f} tok/s" if answered.tokens_per_s else ""
    where = f"{answered.provider} ({answered.model})"
    return f"{where} · primer token {ttft} · total {answered.total_s:.1f} s{speed}"
