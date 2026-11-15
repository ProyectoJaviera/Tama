"""Herramienta del modo conversación por voz: `terminar_conversacion`.

Solo existe mientras Renzo conversa por voz (el navegador lo indica en cada mensaje). El modelo
decide cuándo llamarla (regla 3: nada de buscar "chao" en el texto); la web, al verla, termina el
modo después de que Tama se despida.
"""

from typing import Any

from tama.mcp_hub import ToolInfo, ToolOutcome
from tama.selector import ALWAYS

END_CONVERSATION = "terminar_conversacion"

END_TOOL = ToolInfo(
    server="voz",
    name=END_CONVERSATION,
    description=(
        "Termina la conversación por voz: Tama deja de escuchar sola. Llámala cuando Renzo se "
        "despida o pida terminar o dejar de conversar; luego despídete en una frase corta."
    ),
    input_schema={"type": "object", "properties": {}},
    tier=ALWAYS,
)


class VoiceTools:
    """Envuelve las herramientas de Tama y agrega `terminar_conversacion`."""

    def __init__(self, tools: Any) -> None:
        self._tools = tools

    def available_tools(self, max_tools: int = 10) -> tuple[list[Any], list[str]]:
        if self._tools is None:
            return [END_TOOL], []
        available, down = self._tools.available_tools(max_tools)
        return [*available, END_TOOL], down

    def find(self, tool_name: str) -> Any | None:
        if tool_name == END_CONVERSATION:
            return END_TOOL
        return self._tools.find(tool_name) if self._tools is not None else None

    def call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if tool_name == END_CONVERSATION:
            return ToolOutcome(True, "Listo: la conversación por voz termina. Despídete breve.")
        return self._tools.call(tool_name, arguments)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._tools, name)
