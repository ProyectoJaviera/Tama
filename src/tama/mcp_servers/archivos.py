"""Servidor MCP de archivos: solo dentro de la carpeta de trabajo (TAMA_WORKDIR).

Se lanza por stdio: `python -m tama.mcp_servers.archivos`. Ninguna ruta puede salir de la carpeta.
"""

import fnmatch
import os
from pathlib import Path, PurePosixPath, PureWindowsPath

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

MAX_READ_CHARS = 8000
MAX_RESULTS = 50
TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".toml", ".py", ".log", ".ini"}

server = MCPServer("archivos", instructions="Archivos de la carpeta de trabajo de Renzo.")


class OutsideWorkdir(ValueError):
    pass


def workdir() -> Path:
    root = Path(os.environ.get("TAMA_WORKDIR", "data/trabajo")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_path(relative: str) -> Path:
    """Resuelve `relative` dentro de la carpeta de trabajo; falla si sale de ella."""
    root = workdir()
    candidate = Path(relative.strip() or ".")
    # Rutas absolutas o con unidad (C:\, \\servidor) se rechazan igual en Windows y en Linux.
    windows, posix = PureWindowsPath(relative.strip()), PurePosixPath(relative.strip())
    if windows.drive or windows.root or posix.is_absolute() or candidate.is_absolute():
        raise OutsideWorkdir(
            f"Solo se permiten rutas relativas a la carpeta de trabajo: {relative}"
        )
    resolved = (root / candidate).resolve()  # resuelve también enlaces simbólicos
    if resolved != root and root not in resolved.parents:
        raise OutsideWorkdir(f"La ruta sale de la carpeta de trabajo: {relative}")
    return resolved


def _relative(path: Path) -> str:
    return path.relative_to(workdir()).as_posix()


@server.tool(
    description=(
        "Busca archivos en la carpeta de trabajo. `pattern` filtra por nombre (p. ej. '*.md'); "
        "`text` busca ese texto dentro de los archivos. Sin filtros, lista todos."
    ),
    annotations=ToolAnnotations(read_only_hint=True),
)
def find_files(pattern: str = "*", text: str = "") -> str:
    root = workdir()
    found = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or not fnmatch.fnmatch(path.name.lower(), (pattern or "*").lower()):
            continue
        if text:
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                if text.lower() not in path.read_text(encoding="utf-8", errors="ignore").lower():
                    continue
            except OSError:
                continue
        found.append(f"{_relative(path)} ({path.stat().st_size} bytes)")
        if len(found) >= MAX_RESULTS:
            found.append("… (hay más resultados)")
            break
    return "\n".join(found) if found else "No se encontraron archivos."


@server.tool(
    description="Lee un archivo de texto de la carpeta de trabajo (ruta relativa).",
    annotations=ToolAnnotations(read_only_hint=True),
)
def read_file(path: str) -> str:
    try:
        target = safe_path(path)
    except OutsideWorkdir as exc:
        return str(exc)
    if not target.is_file():
        return f"No existe el archivo {path}."
    content = target.read_text(encoding="utf-8", errors="replace")
    if len(content) > MAX_READ_CHARS:
        return content[:MAX_READ_CHARS] + f"\n… (recortado: {len(content)} caracteres en total)"
    return content


@server.tool(description="Crea un archivo de texto nuevo en la carpeta de trabajo. No sobrescribe.")
def create_file(path: str, content: str) -> str:
    try:
        target = safe_path(path)
    except OutsideWorkdir as exc:
        return str(exc)
    if target.exists():
        return f"Ya existe {path}. Para reemplazarlo usa overwrite_file."
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Archivo creado: {_relative(target)}"


@server.tool(
    description="Reemplaza todo el contenido de un archivo existente de la carpeta de trabajo.",
    annotations=ToolAnnotations(destructive_hint=True),
)
def overwrite_file(path: str, content: str) -> str:
    try:
        target = safe_path(path)
    except OutsideWorkdir as exc:
        return str(exc)
    if not target.is_file():
        return f"No existe el archivo {path}. Para crearlo usa create_file."
    target.write_text(content, encoding="utf-8")
    return f"Archivo reemplazado: {_relative(target)}"


if __name__ == "__main__":
    server.run("stdio")
