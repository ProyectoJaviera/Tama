"""Selector de herramientas: pocas a la vista en cada turno para cuidar el contexto.

No interpreta el mensaje de Renzo (regla 3): van siempre las esenciales, luego lo usado en el turno
anterior y las de uso común. Para el resto, el propio modelo pide un grupo con `abrir_herramientas`.
"""

from dataclasses import dataclass, field
from typing import Any

OPENER = "abrir_herramientas"
ALWAYS, BASE, EXTRA = "siempre", "base", "extra"


@dataclass
class ToolFocus:
    """Lo que el modelo usó o abrió en el turno anterior; sigue a la vista en el siguiente."""

    groups: set[str] = field(default_factory=set)
    used: set[str] = field(default_factory=set)


def select(tools: list[Any], limit: int, focus: ToolFocus | None = None) -> tuple[list[Any], bool]:
    """Elige hasta `limit` herramientas; el segundo valor indica si quedaron algunas ocultas.

    Si quedan ocultas, se reserva un espacio para `abrir_herramientas`. Se mantiene el orden de la
    configuración: así el prefijo del prompt cambia lo menos posible entre turnos.
    """
    if len(tools) <= limit:
        return tools, False
    focus = focus or ToolFocus()
    ranked = (
        [t for t in tools if t.tier == ALWAYS]
        + [t for t in tools if t.name in focus.used]
        + [t for t in tools if t.group_name in focus.groups]
        + [t for t in tools if t.tier == BASE]
    )
    chosen = list(dict.fromkeys(t.name for t in ranked))[: limit - 1]
    return [t for t in tools if t.name in chosen], True


def opener_spec(tools: list[Any], visible: list[Any]) -> dict[str, Any]:
    """Herramienta para pedir un grupo que no está a la vista, con lo que contiene cada uno."""
    shown = {t.name for t in visible}
    groups: dict[str, list[str]] = {}
    for tool in tools:
        if tool.name not in shown:
            groups.setdefault(tool.group_name, []).append(tool.name)
    listing = "; ".join(f"{group}: {', '.join(names)}" for group, names in groups.items())
    return {
        "type": "function",
        "function": {
            "name": OPENER,
            "description": (
                "Abre un grupo de herramientas que no tienes a la vista. Llámala antes de "
                f"responder si necesitas una. Grupos: {listing}."
            ),
            "parameters": {
                "type": "object",
                "properties": {"grupo": {"type": "string", "enum": sorted(groups)}},
                "required": ["grupo"],
            },
        },
    }
