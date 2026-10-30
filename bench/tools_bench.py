"""Benchmark de llamadas a herramientas con muchas herramientas a la vez (Sprint 2, punto 7).

Mide, por modelo y por escalón de 5, 10, 20 y 40 herramientas visibles: % de casos correctos
(herramienta, argumentos, cadenas de 2-3 llamadas y no llamar cuando no corresponde), latencia y
errores de formato. Las herramientas no se ejecutan: devuelven resultados de ejemplo.

Uso:
    uv run python bench/tools_bench.py --write gemma4:31b-cloud glm-5.3:cloud
    uv run python bench/tools_bench.py <modelos...> --escalones 40 --write   # cribado rápido
"""

import argparse
import json
import random
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import tama  # noqa: F401  (quita el SSLKEYLOGFILE de Avast)
from tama import prompts
from tama.ollama import OllamaClient, OllamaError

BENCH_DIR = Path(__file__).parent
REAL_TOOLS = json.loads((BENCH_DIR / "tools_real.json").read_text(encoding="utf-8"))
CORE = ["get_current_time", "create_note", "search_notes", "read_file", "fetch"]
STEPS = [5, 10, 20, 40]
MAX_CALL_ROUNDS = 4


def tool(
    tool_name: str, description: str, required: list[str] | None = None, /, **params: str
) -> dict:
    props = {p: {"type": "string", "description": d} for p, d in params.items()}
    return {
        "server": "relleno",
        "type": "function",
        "function": {
            "name": tool_name,
            "description": description,
            "parameters": {"type": "object", "properties": props, "required": required or []},
        },
    }


# Herramientas de relleno plausibles: algunas se parecen a las reales a propósito.
FILLER = [
    tool("weather_forecast", "Pronóstico del tiempo de una ciudad.", ["city"], city="Ciudad"),
    tool(
        "calendar_create_event",
        "Crea un evento en el calendario.",
        ["title", "start"],
        title="Título",
        start="Fecha y hora de inicio",
    ),
    tool("calendar_list_events", "Lista los eventos del calendario de un día.", date="Fecha"),
    tool(
        "send_email",
        "Envía un correo electrónico.",
        ["to", "subject", "body"],
        to="Destinatario",
        subject="Asunto",
        body="Cuerpo",
    ),
    tool("contacts_search", "Busca un contacto en la agenda.", ["name"], name="Nombre"),
    tool("play_music", "Reproduce música.", ["query"], query="Canción, artista o lista"),
    tool("set_timer", "Pone un temporizador.", ["minutes"], minutes="Minutos"),
    tool(
        "reminder_create",
        "Crea un recordatorio con aviso a una hora.",
        ["text", "when"],
        text="Qué recordar",
        when="Cuándo avisar",
    ),
    tool(
        "translate_text",
        "Traduce un texto.",
        ["text", "target_language"],
        text="Texto",
        target_language="Idioma destino",
    ),
    tool(
        "convert_units",
        "Convierte unidades de medida.",
        ["value", "from_unit", "to_unit"],
        value="Valor",
        from_unit="Unidad origen",
        to_unit="Unidad destino",
    ),
    tool("calculator", "Evalúa una expresión aritmética.", ["expression"], expression="Expresión"),
    tool("news_headlines", "Titulares de noticias de un tema.", topic="Tema"),
    tool(
        "recipe_search",
        "Busca recetas por ingredientes.",
        ["ingredients"],
        ingredients="Ingredientes",
    ),
    tool(
        "shopping_list_add", "Agrega un producto a la lista de compras.", ["item"], item="Producto"
    ),
    tool("shopping_list_show", "Muestra la lista de compras."),
    tool(
        "smart_lights_set",
        "Enciende, apaga o regula las luces.",
        ["room"],
        room="Habitación",
        on="true/false",
        brightness="0-100",
    ),
    tool(
        "thermostat_set",
        "Ajusta la temperatura del termostato.",
        ["temperature"],
        temperature="Grados",
    ),
    tool(
        "maps_route",
        "Calcula una ruta entre dos lugares.",
        ["origin", "destination"],
        origin="Origen",
        destination="Destino",
    ),
    tool("wikipedia_search", "Busca un tema en Wikipedia.", ["query"], query="Tema"),
    tool(
        "currency_convert",
        "Convierte entre monedas.",
        ["amount", "from_currency", "to_currency"],
        amount="Monto",
        from_currency="Moneda origen",
        to_currency="Moneda destino",
    ),
    tool("alarm_set", "Pone una alarma.", ["time"], time="Hora"),
    tool("todo_add", "Agrega una tarea a la lista de pendientes.", ["task"], task="Tarea"),
    tool("todo_list", "Muestra la lista de tareas pendientes."),
    tool("habit_log", "Registra que se cumplió un hábito.", ["habit"], habit="Hábito"),
    tool(
        "expense_add",
        "Registra un gasto.",
        ["amount", "category"],
        amount="Monto",
        category="Categoría",
    ),
    tool("photo_search", "Busca fotos en la galería.", ["query"], query="Qué buscar"),
    tool("system_volume_set", "Ajusta el volumen del computador.", ["level"], level="0-100"),
    tool("read_clipboard", "Lee el contenido del portapapeles."),
    tool("web_search", "Busca en internet y devuelve resultados.", ["query"], query="Consulta"),
    tool("bookmark_add", "Guarda un marcador web.", ["url"], url="URL", title="Título"),
]

FAKE_RESULTS = {
    "get_current_time": '{"timezone": "America/Santiago", "datetime": "2026-10-29T18:05:00-03:00"}',
    "search_notes": (
        "[nota 12] Dentista (actualizada 2026-10-20)\nHora con el dentista el martes 9:00\n\n"
        "[nota 4] Viaje a Chiloé (actualizada 2026-10-18)\nSalir el 5 de enero, reservar cabaña"
    ),
    "read_file": "Tareas:\n- pagar la luz (urgente)\n- regar las plantas\n- llamar a Pedro",
    "find_files": "plan.md (120 bytes)\npresupuesto.md (340 bytes)\ntareas.txt (80 bytes)",
    "fetch": "Contents of https://example.com/: Example Domain. This domain is for use in "
    "documentation examples.",
}


@dataclass
class Expect:
    tool: str
    check: Callable[[dict], bool] | None = None


@dataclass
class Case:
    id: str
    prompt: str
    expect: list[Expect] = field(default_factory=list)  # vacío: no debe llamar herramientas

    @property
    def tools_needed(self) -> set[str]:
        return {e.tool for e in self.expect}


def has(key: str, *words: str) -> Callable[[dict], bool]:
    def check(args: dict) -> bool:
        value = str(args.get(key, "")).lower()
        return all(w in value for w in words)

    return check


def any_has(*words: str) -> Callable[[dict], bool]:
    def check(args: dict) -> bool:
        value = " ".join(str(v) for v in args.values()).lower()
        return all(w in value for w in words)

    return check


def is_id(key: str, value: int) -> Callable[[dict], bool]:
    return lambda args: str(args.get(key, "")).strip() == str(value)


CASES = [
    Case("hora", "¿Qué hora es?", [Expect("get_current_time")]),
    Case(
        "hora-tokio",
        "¿Qué hora es ahora en Tokio?",
        [Expect("get_current_time", has("timezone", "tokyo"))],
    ),
    Case(
        "hora-madrid",
        "¿Qué hora es en Madrid?",
        [Expect("get_current_time", has("timezone", "madrid"))],
    ),
    Case(
        "nota-filamento",
        "Anota que mañana compro filamento PLA negro",
        [Expect("create_note", any_has("filamento"))],
    ),
    Case(
        "nota-titulo",
        "Guarda una nota titulada 'Ideas' con el texto: soporte para el teléfono",
        [Expect("create_note", has("title", "ideas"))],
    ),
    Case(
        "nota-banco", "Anota: llamar al banco el lunes", [Expect("create_note", any_has("banco"))]
    ),
    Case(
        "buscar-impresora",
        "¿Qué tenía anotado sobre la impresora 3D?",
        [Expect("search_notes", has("query", "impresora"))],
    ),
    Case(
        "buscar-mama",
        "Busca mis notas donde hablo de mi mamá",
        [Expect("search_notes", has("query", "mam"))],
    ),
    Case("notas-recientes", "Muéstrame mis notas más recientes", [Expect("search_notes")]),
    Case(
        "editar-nota",
        "Cambia el texto de la nota 4 por: comprar 2 rollos",
        [Expect("update_note", is_id("note_id", 4))],
    ),
    Case("borrar-nota", "Borra la nota 7", [Expect("delete_note", is_id("note_id", 7))]),
    Case(
        "archivos-md",
        "¿Qué archivos markdown hay en mi carpeta de trabajo?",
        [Expect("find_files", has("pattern", "md"))],
    ),
    Case(
        "archivo-presupuesto",
        "¿Cuál de mis archivos menciona la palabra presupuesto?",
        [Expect("find_files", any_has("presupuesto"))],
    ),
    Case("leer-plan", "Lee el archivo plan.md", [Expect("read_file", has("path", "plan.md"))]),
    Case(
        "leer-config",
        "Muéstrame qué dice config.toml",
        [Expect("read_file", has("path", "config.toml"))],
    ),
    Case(
        "crear-compras",
        "Crea un archivo compras.txt con: pan, leche",
        [Expect("create_file", has("path", "compras.txt"))],
    ),
    Case(
        "reemplazar",
        "Reemplaza el contenido de notas.txt por 'vacío'",
        [Expect("overwrite_file", has("path", "notas.txt"))],
    ),
    Case(
        "web-example",
        "Lee esta página y dime de qué trata: https://example.com",
        [Expect("fetch", has("url", "example.com"))],
    ),
    Case(
        "web-about",
        "Abre https://example.org/about y resúmela",
        [Expect("fetch", has("url", "example.org"))],
    ),
    Case("saludo", "Hola Tama, ¿cómo estás?"),
    Case("opinion", "¿Qué opinas de aprender Rust este año?"),
    Case("gracias", "Gracias, eso es todo por hoy"),
    Case("fotosintesis", "Explícame qué es la fotosíntesis en una frase"),
    Case("chiste", "Cuéntame un chiste corto"),
    Case(
        "cadena-viaje",
        "Busca mis notas sobre el viaje y guarda un resumen en el archivo viaje.md",
        [
            Expect("search_notes", has("query", "viaje")),
            Expect("create_file", has("path", "viaje.md")),
        ],
    ),
    Case(
        "cadena-tareas",
        "Lee el archivo tareas.txt y crea una nota con lo más urgente",
        [Expect("read_file", has("path", "tareas.txt")), Expect("create_note", any_has("luz"))],
    ),
    Case(
        "cadena-hora",
        "Dime qué hora es y anota que a esa hora empecé a estudiar",
        [Expect("get_current_time"), Expect("create_note", any_has("estudi"))],
    ),
    Case(
        "cadena-web",
        "Lee https://example.com y guarda lo principal en una nota",
        [Expect("fetch", has("url", "example.com")), Expect("create_note")],
    ),
    Case(
        "cadena-dentista",
        "Busca la nota del dentista y cámbiale el texto a 'jueves 10:00'",
        [
            Expect("search_notes", has("query", "dentista")),
            Expect("update_note", is_id("note_id", 12)),
        ],
    ),
]


class QuotaError(RuntimeError):
    """Ollama limitó o bloqueó el uso de modelos cloud: hay que detenerse y avisar a Renzo."""


def tools_for_step(step: int, seed: int = 7) -> list[dict]:
    """Las 5 principales, luego las 10 reales, luego relleno; en orden mezclado (fijo)."""
    real = {t["function"]["name"]: t for t in REAL_TOOLS}
    chosen = [real[n] for n in CORE] if step <= 5 else list(REAL_TOOLS)
    chosen += FILLER[: max(0, step - len(chosen))]
    random.Random(seed + step).shuffle(chosen)
    return [{"type": t["type"], "function": t["function"]} for t in chosen[:step]]


def is_quota_error(exc: OllamaError) -> bool:
    text = str(exc).lower()
    return exc.status in (402, 429) or any(
        w in text for w in ("quota", "rate limit", "usage limit", "upgrade", "subscription")
    )


def run_case(client: OllamaClient, model: str, case: Case, tools: list[dict]) -> dict:
    names = {t["function"]["name"] for t in tools}
    messages = [
        {"role": "system", "content": prompts.identity()},
        {"role": "user", "content": case.prompt},
    ]
    calls: list[tuple[str, dict]] = []
    format_errors = 0
    start = time.perf_counter()
    first_latency = None
    for _ in range(MAX_CALL_ROUNDS):
        try:
            response = client.chat(model, messages, tools=tools)
        except OllamaError as exc:
            if is_quota_error(exc):
                raise QuotaError(str(exc)) from exc
            return {"ok": False, "error": str(exc)[:200], "latency": None, "format_errors": 0}
        first_latency = first_latency or time.perf_counter() - start
        message = response.get("message", {})
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            break
        messages.append(
            {"role": "assistant", "content": message.get("content", ""), "tool_calls": tool_calls}
        )
        for call in tool_calls:
            fn = call.get("function", {})
            name, args = fn.get("name", ""), fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args or "{}")
                except ValueError:
                    format_errors += 1
                    args = {}
            if name not in names or not isinstance(args, dict):
                format_errors += 1
                args = args if isinstance(args, dict) else {}
            calls.append((name, args))
            messages.append(
                {"role": "tool", "tool_name": name, "content": FAKE_RESULTS.get(name, "ok")}
            )
    return {
        "ok": judge(case, calls),
        "calls": [n for n, _ in calls],
        "latency": first_latency,
        "format_errors": format_errors,
    }


def judge(case: Case, calls: list[tuple[str, dict]]) -> bool:
    """Correcto si llama las herramientas esperadas en orden (con argumentos válidos)."""
    if not case.expect:
        return not calls
    position = 0
    for name, args in calls:
        expected = case.expect[position]
        if name == expected.tool and (expected.check is None or expected.check(args)):
            position += 1
            if position == len(case.expect):
                return True
    return False


def bench(client: OllamaClient, model: str, step: int) -> dict:
    tools = tools_for_step(step)
    names = {t["function"]["name"] for t in tools}
    cases = [c for c in CASES if c.tools_needed <= names]
    rows = []
    for case in cases:
        result = run_case(client, model, case, tools)
        rows.append({"case": case.id, **result})
    latencies = [r["latency"] for r in rows if r.get("latency")]
    ok = sum(1 for r in rows if r["ok"])
    return {
        "casos": len(rows),
        "correctos": ok,
        "pct": round(100 * ok / len(rows), 1) if rows else 0.0,
        "latencia_media_s": round(statistics.mean(latencies), 2) if latencies else None,
        "errores_formato": sum(r.get("format_errors", 0) for r in rows),
        "errores_api": sum(1 for r in rows if r.get("error")),
        "fallos": [
            f"{r['case']}: {r.get('error') or ('llamó ' + ', '.join(r.get('calls') or ['nada']))}"
            for r in rows
            if not r["ok"]
        ],
    }


def ensure_model(model: str) -> str | None:
    """Trae el manifiesto del modelo cloud (unos KB, sin pesos). Devuelve el error, si hay."""
    proc = subprocess.run(["ollama", "pull", model], capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout).strip().splitlines()[-1][:200]
    return None


def load_results(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def to_markdown(results: dict) -> str:
    lines = [
        "# Herramientas: modelos cloud de Ollama con muchas herramientas a la vez",
        "",
        f"- Generado: {date.today().isoformat()} con `bench/tools_bench.py`, vía el Ollama local",
        "  (sesión de `ollama signin`). Las herramientas no se ejecutan: devuelven resultados de",
        "  ejemplo. Escalones: 5, 10, 20 y 40 herramientas visibles (las reales más relleno",
        "  plausible, en orden mezclado). Por escalón, todos los casos cuyas herramientas están",
        "  a la vista (21 en el escalón de 5, 29 en los demás): herramienta correcta, argumentos,",
        "  cadenas de 2-3 llamadas y no llamar cuando no corresponde.",
        "",
        "| Modelo | 5 | 10 | 20 | 40 | Latencia media (s) | Errores de formato | Errores API |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for model, data in results.items():
        if "error" in data:
            lines.append(f"| {model} | no disponible: {data['error']} | | | | | | |")
            continue
        cells = []
        for step in STEPS:
            r = data.get(str(step))
            cells.append(f"{r['pct']:.0f}% ({r['correctos']}/{r['casos']})" if r else "—")
        steps = [data[s] for s in map(str, STEPS) if s in data]
        lat = [s["latencia_media_s"] for s in steps if s["latencia_media_s"]]
        lines.append(
            f"| {model} | {' | '.join(cells)} | {statistics.mean(lat):.1f} | "
            f"{sum(s['errores_formato'] for s in steps)} | {sum(s['errores_api'] for s in steps)} |"
            if lat
            else f"| {model} | {' | '.join(cells)} | — | — | — |"
        )
    lines += ["", "## Fallos por modelo y escalón", ""]
    for model, data in results.items():
        for step in STEPS:
            r = data.get(str(step))
            if r and r["fallos"]:
                lines.append(f"- `{model}` ({step}): " + "; ".join(r["fallos"]))
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("models", nargs="+")
    parser.add_argument("--escalones", nargs="+", type=int, default=STEPS)
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    out = BENCH_DIR / "tools_results.json"
    results = load_results(out) if args.write else {}
    with OllamaClient(args.host, timeout=180) as client:
        for model in args.models:
            problem = ensure_model(model)
            if problem:
                results[model] = {"error": problem}
                print(f"[no disponible] {model}: {problem}", flush=True)
                continue
            results.setdefault(model, {})
            for step in args.escalones:
                try:
                    r = bench(client, model, step)
                except QuotaError as exc:
                    print(f"\n[LÍMITE DE OLLAMA] {model}: {exc}\nSe detiene el benchmark.")
                    if args.write:
                        out.write_text(
                            json.dumps(results, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8",
                        )
                        (BENCH_DIR / "TOOLS_RESULTS.md").write_text(
                            to_markdown(results), encoding="utf-8"
                        )
                    return 2
                results[model][str(step)] = r
                print(
                    f"[{model}] {step} herramientas: {r['pct']}% ({r['correctos']}/{r['casos']})"
                    f" · {r['latencia_media_s']} s · formato {r['errores_formato']}"
                    f" · api {r['errores_api']}",
                    flush=True,
                )
                if args.write:
                    out.write_text(
                        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                    )
                    (BENCH_DIR / "TOOLS_RESULTS.md").write_text(
                        to_markdown(results), encoding="utf-8"
                    )
    print(to_markdown(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
