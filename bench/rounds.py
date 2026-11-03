"""Media y rango de las evals repetidas del banco local (`resumen.json` y `resumen-rN.json`).

La rectificación de la ronda 1 se hizo antes de arreglar `update_observation` y no se promedia.

Uso:
    uv run python bench/rounds.py
"""

import json
import statistics
import sys
from pathlib import Path

OUT = Path(__file__).parent / "local"
KEYS = [
    ("Memoria", "memoria", "hechos"),
    ("Corrección", "correccion", "checks"),
    ("Rectificación", "rectificacion", "correctas"),
    ("Herramientas", "herramientas", "correctos"),
    ("Reflexión: duplicados", "reflexion", "duplicados"),
    ("Reflexión: contradicciones", "reflexion", "contradicciones"),
]


def fraction(value: object) -> tuple[int, int] | None:
    try:
        done, total = str(value).split("/")
        return int(done), int(total)
    except ValueError:
        return None


def collect(folder: Path) -> dict[str, list[tuple[int, int]]]:
    found: dict[str, list[tuple[int, int]]] = {}
    for path in sorted(folder.glob("resumen*.json")):
        first = path.name == "resumen.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for label, name, key in KEYS:
            if first and name == "rectificacion":
                continue
            item = data.get(name, {})
            value = fraction(item.get(key)) if item.get("estado") == "ok" else None
            if value:
                found.setdefault(label, []).append(value)
    return found


def render() -> str:
    lines = [
        "| Modelo | Eval | Rondas | Media | Rango |",
        "|---|---|---:|---:|---|",
    ]
    for folder in sorted(OUT.iterdir()):
        if not folder.is_dir() or len(list(folder.glob("resumen-r*.json"))) == 0:
            continue
        model = json.loads((folder / "resumen.json").read_text(encoding="utf-8"))["modelo"]
        for label, values in collect(folder).items():
            if len(values) < 2:
                continue
            total = values[0][1]
            scores = [done for done, _ in values]
            mean = statistics.mean(scores)
            lines.append(
                f"| {model} | {label} | {len(scores)} | {mean:.1f}/{total} | "
                f"{min(scores)} a {max(scores)} |"
            )
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
