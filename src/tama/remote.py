"""Qué puede hacer Tama cuando Renzo entra desde fuera del PC (por Tailscale).

"Local" es una petición directa a 127.0.0.1 en el PC; todo lo que llega por `tailscale serve` es
remoto, aunque el celular esté en el WiFi de la casa. En remoto, el modelo no ve estas herramientas
y, si igual las pide, el servidor las rechaza: la restricción no depende del prompt.

- Archivos del PC: ninguna (buscar, leer, crear ni sobrescribir).
- Lo que borra para siempre: borrar entidades, datos, relaciones, notas u objetivos y `/olvidar`.

Lo que conserva el historial sigue disponible, con la confirmación de siempre: corregir un dato
(queda su "antes"), editar una nota (queda su versión anterior), descartar un objetivo, cancelar
un recordatorio o proponer un cambio al perfil.
"""

from typing import Any

from tama.mcp_hub import ToolOutcome

FILE_TOOLS = frozenset({"find_files", "read_file", "create_file", "overwrite_file"})
LOSSY_TOOLS = frozenset(
    {
        "delete_entities",
        "delete_observations",
        "delete_relations",
        "forget_memory",
        "delete_goal",
        "delete_note",
    }
)
BLOCKED = FILE_TOOLS | LOSSY_TOOLS
# Va en la línea de "herramientas no disponibles" del prompt, solo en remoto.
NOTICE = (
    "archivos del PC y borrar: Renzo escribe desde fuera del PC. Si los pide, dile que eso se "
    "hace desde el PC; no ofrezcas borrar aquí ni busques otra forma. Sí puedes corregir datos "
    "y descartar objetivos"
)
REFUSAL = (
    "Bloqueado: Renzo escribe desde fuera del PC, y los archivos y lo que borra para siempre solo "
    "se pueden usar desde el PC. Díselo con naturalidad; no lo intentes de otra forma."
)


class RemoteTools:
    """Envuelve las herramientas de Tama y deja fuera lo que solo se hace en el PC."""

    def __init__(self, tools: Any) -> None:
        self._tools = tools

    def available_tools(self, max_tools: int = 10) -> tuple[list[Any], list[str]]:
        available, down = self._tools.available_tools(max_tools)
        return [t for t in available if t.name not in BLOCKED], [*down, NOTICE]

    def find(self, tool_name: str) -> Any | None:
        return None if tool_name in BLOCKED else self._tools.find(tool_name)

    def call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if tool_name in BLOCKED:
            return ToolOutcome(False, REFUSAL)
        return self._tools.call(tool_name, arguments)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._tools, name)
