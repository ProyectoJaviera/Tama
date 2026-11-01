"""Comandos con barra: los usan la CLI y el chat web, sin pasar por el modelo."""

import json
from datetime import date
from difflib import get_close_matches
from pathlib import Path

from tama.brain import Brain
from tama.mcp_servers import memoria
from tama.reflection import diary_path

NO_MEMORY = "Sin memoria: los servidores MCP no están activos."
COMMANDS = {
    "/memoria": "lo vigente; con un nombre, toda esa ficha",
    "/memoria historial": "línea de tiempo de un tema",
    "/objetivos": "todos los objetivos",
    "/diario": "el diario de hoy, o de una fecha AAAA-MM-DD",
    "/olvidar": "borra lo que recuerde sobre un tema",
    "/herramientas": "estado de las herramientas",
    "/proveedor": "capas, herramientas y sugerencias",
    "/estado": "lo mismo que /proveedor",
    "/ayuda": "esta lista",
}
HELP = "Comandos:\n" + "\n".join(f"{name}: {what}" for name, what in COMMANDS.items())


def memory_text(brain: Brain, args: str = "") -> str:
    """`/memoria` da lo vigente; con una ficha, todos sus datos; `historial`, la línea de tiempo."""
    if brain.tools is None:
        return NO_MEMORY
    if args.startswith("histori"):
        tema = args.split(maxsplit=1)[1] if " " in args else ""
        return brain.tools.call("linea_de_tiempo", {"tema": tema}).text
    if args:
        return brain.tools.call("open_nodes", {"names": [args]}).text
    return brain.tools.call("search_nodes", {"query": ""}).text


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
    return (
        f"Capas:\n{layers_text(brain)}\n\nHerramientas:\n{tools_text(brain)}\n\n"
        f"Sugerencias pendientes:\n{suggestions}"
    )


def closest(name: str) -> str:
    """Comando desconocido: sugiere el más parecido."""
    found = get_close_matches(name, COMMANDS, n=1, cutoff=0.4)
    if found:
        return f"No conozco {name}. ¿Quisiste decir {found[0]}? Con /ayuda ves la lista."
    return f"No conozco {name}. {HELP}"


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
    name, _, args = text.strip().partition(" ")
    args = args.strip()
    name = name.lower()
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
    if name == "/ayuda":
        return HELP
    return closest(name)
