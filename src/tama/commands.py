"""Comandos con barra: los usan la CLI y el chat web, sin pasar por el modelo."""

from datetime import date
from pathlib import Path

from tama.brain import Brain
from tama.mcp_servers import memoria
from tama.reflection import diary_path

NO_MEMORY = "Sin memoria: los servidores MCP no están activos."
HELP = (
    "Comandos: /memoria [ficha], /memoria historial <tema>, /objetivos, /diario [AAAA-MM-DD], "
    "/estado, /proveedor, /herramientas y /ayuda."
)


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


def run(brain: Brain, text: str, memory_dir: Path) -> str:
    """Responde un comando del chat web."""
    name, _, args = text.strip().partition(" ")
    args = args.strip()
    name = name.lower()
    if name == "/memoria":
        return memory_text(brain, args)
    if name == "/objetivos":
        return goals_text(brain)
    if name == "/diario":
        return diary_text(memory_dir, args)
    if name == "/estado":
        return status_text(brain)
    if name == "/proveedor":
        return layers_text(brain)
    if name == "/herramientas":
        return tools_text(brain)
    if name == "/olvidar":
        return "Para olvidar algo, abre su ficha en la pestaña Memoria."
    return HELP
