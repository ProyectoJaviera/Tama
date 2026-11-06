"""Comandos con barra: los usan la CLI y el chat web, sin pasar por el modelo."""

import json
from datetime import date, timedelta
from difflib import get_close_matches
from pathlib import Path

from tama.brain import Brain
from tama.mcp_servers import memoria
from tama.proactive import morning_summary
from tama.reflection import diary_path

NO_MEMORY = "Sin memoria: los servidores MCP no están activos."
CARDS_PAGE = 20
FACTS_PAGE = 10
COMMANDS = {
    "/memoria": "las fichas; con un nombre, toda esa ficha (por páginas: /memoria Lucía 2)",
    "/memoria historial": "línea de tiempo de un tema",
    "/objetivos": "todos los objetivos",
    "/diario": "el diario de hoy, o de una fecha AAAA-MM-DD",
    "/olvidar": "borra lo que recuerde sobre un tema",
    "/recordatorios": "los pendientes y los de hoy, con su estado",
    "/cancelar": "cancela un recordatorio por su texto o número, con confirmación",
    "/avisos": "últimos avisos; con `resumen`, el balance semanal",
    "/silencio": "pausa los avisos por N horas (por defecto 2); `no` los reactiva",
    "/resumen": "el resumen de hoy",
    "/herramientas": "estado de las herramientas",
    "/proveedor": "capas, herramientas y sugerencias",
    "/estado": "lo mismo que /proveedor",
    "/ayuda": "esta lista",
}
HELP = "Comandos:\n" + "\n".join(f"{name}: {what}" for name, what in COMMANDS.items())
ALIASES = {"/recordatorio": "/recordatorios", "/cancela": "/cancelar"}


def canonical(text: str) -> str:
    """El comando con su nombre oficial: /cancela 2 es /cancelar 2."""
    name, space, args = text.strip().partition(" ")
    return ALIASES.get(name.lower(), name.lower()) + space + args.strip()


def facts(count: int) -> str:
    """ "1 dato", "3 datos"."""
    return f"{count} dato" + ("" if count == 1 else "s")


def split_page(args: str) -> tuple[str, int]:
    """`Lucía 2` → ("Lucía", 2); sin número, la página 1."""
    words = args.split()
    if words and words[-1].isdigit():
        return " ".join(words[:-1]), int(words[-1])
    return args.strip(), 1


def cards_text(page: int) -> str:
    """Todas las fichas con cuántos datos tienen, de a CARDS_PAGE."""
    cards = memoria.list_cards()
    if not cards:
        return "Todavía no hay nada guardado."
    last = memoria.pages(len(cards), CARDS_PAGE)
    page = min(max(1, page), last)
    shown = cards[(page - 1) * CARDS_PAGE : page * CARDS_PAGE]
    lines = [f"- {c['name']} ({c['type']}): {facts(c['facts'])}" for c in shown]
    lines.append("Para ver una ficha entera: /memoria <nombre>.")
    if last > 1:
        lines.append(
            f"Página {page} de {last}" + (f": /memoria {page + 1}" if page < last else ".")
        )
    return "\n".join([f"Fichas ({len(cards)}):", *lines])


def card_text(name: str, page: int) -> str:
    """Todos los datos de una ficha, de a FACTS_PAGE, pedida por su nombre."""
    found = memoria.find_card(name)
    data = memoria.card(found, page, FACTS_PAGE) if found else None
    if data is None:
        return f"No encontré una ficha llamada «{name}». Con /memoria ves la lista."
    first = (data["page"] - 1) * FACTS_PAGE
    shown = f"{first + 1}-{first + len(data['facts'])} de " if data["pages"] > 1 else ""
    lines = [f"{data['name']} ({data['type']}), {shown}{facts(data['total'])}:"]
    for fact in data["facts"]:
        lines.append(f"- {fact['text']} ({fact['label']})")
        if fact["before"]:
            lines.append(f"  antes: {fact['before']}")
    if data["page"] == 1:
        lines += [f"- relación: {r}" for r in data["relations"]]
    if data["page"] < data["pages"]:
        lines.append(
            f"Página {data['page']} de {data['pages']}: /memoria {data['name']} {data['page'] + 1}"
        )
    return "\n".join(lines)


def memory_text(brain: Brain, args: str = "") -> str:
    """`/memoria` lista las fichas; con un nombre, todos sus datos por páginas; `historial`, la
    línea de tiempo de un tema."""
    if brain.tools is None:
        return NO_MEMORY
    if args.startswith("histori"):
        tema = args.split(maxsplit=1)[1] if " " in args else ""
        return brain.tools.call("linea_de_tiempo", {"tema": tema}).text
    name, page = split_page(args)
    return card_text(name, page) if name else cards_text(page)


def goals_text(brain: Brain) -> str:
    if brain.tools is None:
        return NO_MEMORY
    return brain.tools.call("list_goals", {"estado": "todos"}).text


def layers_text(brain: Brain) -> str:
    return "\n".join(
        f"{layer.provider.name}  {layer.provider.model}  {state}"
        for layer, state in brain.cascade.status()
    )


def tools_text(brain: Brain) -> str:
    if brain.tools is None:
        return "Sin herramientas configuradas."
    return "\n".join(
        f"{name}  {state}  {', '.join(tools)}" for name, state, tools in brain.tools.status()
    )


def diary_text(memory_dir: Path, args: str) -> str:
    try:
        day = date.fromisoformat(args) if args else date.today()
    except ValueError:
        return "Uso: /diario [AAAA-MM-DD]"
    path = diary_path(memory_dir, day)
    if not path.exists():
        return f"No hay diario del {day.isoformat()}. Puedes escribirlo en la pestaña Diario."
    return path.read_text(encoding="utf-8").strip()


def status_text(brain: Brain) -> str:
    pending = memoria.pending_list()
    suggestions = "\n".join(f"- {s}" for s in pending) or "Ninguna."
    text = (
        f"Capas:\n{layers_text(brain)}\n\nHerramientas:\n{tools_text(brain)}\n\n"
        f"Sugerencias pendientes:\n{suggestions}"
    )
    if brain.proactive is not None and brain.proactive.ntfy_error:
        text += f"\n\nntfy no responde: {brain.proactive.ntfy_error}"
    return text


def notices_text(args: str) -> str:
    today = memoria.local_now().date()
    if args.startswith("resumen"):
        report = memoria.weekly_report(today)
        return "Últimos 7 días: " + ", ".join(f"{v} {k}" for k, v in report.items())
    lines = [
        f"{n['shown_at'][:16].replace('T', ' ')} {n['kind']} ({n['state']}): {n['text']}"
        for n in memoria.list_notices(10)
    ]
    return "\n".join(lines) or "Todavía no hay avisos."


def silence_text(args: str) -> str:
    if args == "no":
        memoria.mute_until(None)
        return "Avisos activos de nuevo."
    hours = int(args) if args.isdigit() else 2
    memoria.mute_until(memoria.local_now() + timedelta(hours=hours))
    return f"Silencio por {hours} horas: solo pasan los urgentes."


def closest(name: str) -> str:
    """Comando desconocido: sugiere el más parecido."""
    found = get_close_matches(name, COMMANDS, n=1, cutoff=0.6)
    if found:
        return f"No conozco {name}. ¿Quisiste decir {found[0]}? Con /ayuda ves la lista."
    return f"No conozco {name}. {HELP}"


def cancel_preview(args: str) -> tuple[str, list[dict]]:
    """Qué cancelaría `/cancelar`: la pregunta si es uno solo, o la explicación y las opciones."""
    if not args:
        return "Uso: /cancelar <qué dice el recordatorio>, por ejemplo /cancelar sacar la ropa.", []
    found, message = memoria.find_to_cancel(args)
    if len(found) == 1:
        return memoria.cancel_question(found[0]), found
    return message, found


def forget_preview(brain: Brain, query: str) -> tuple[str, dict | None]:
    """Lo que `/olvidar` borraría, y los datos para hacerlo si Renzo dice que sí."""
    if brain.tools is None:
        return NO_MEMORY, None
    if not query:
        return "Uso: /olvidar <tema>", None
    outcome = brain.tools.call("find_memory", {"query": query})
    if not outcome.ok:
        return outcome.text, None
    found = json.loads(outcome.text)
    items, messages = found["items"], found["mensajes"]
    if not items and not messages:
        similar = ", ".join(found["parecidas"]) or "ninguna"
        return f"No encontré nada sobre eso. Entidades parecidas: {similar}", None
    lines = [f"- {item['texto']}" for item in items]
    if messages:
        lines.append(f"Además, {messages} mensajes de conversación lo mencionan.")
    arguments = {
        "entidades": [i["entidad"] for i in items if "entidad" in i],
        "datos": [i["dato"] for i in items if "dato" in i],
        "consulta": query,
    }
    return "\n".join(lines), arguments


def run(brain: Brain, text: str, memory_dir: Path) -> str:
    """Responde un comando que no necesita confirmación."""
    name, _, args = canonical(text).partition(" ")
    if name == "/memoria":
        return memory_text(brain, args)
    if name == "/objetivos":
        return goals_text(brain)
    if name == "/diario":
        return diary_text(memory_dir, args)
    if name in ("/estado", "/proveedor"):
        return status_text(brain)
    if name == "/herramientas":
        return tools_text(brain)
    if name == "/recordatorios":
        return memoria.list_reminders()
    if name == "/avisos":
        return notices_text(args)
    if name == "/silencio":
        return silence_text(args)
    if name == "/resumen":
        return morning_summary(memoria.local_now())
    if name == "/ayuda":
        return HELP
    return closest(name)
