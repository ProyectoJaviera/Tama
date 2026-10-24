"""Benchmark de modelos de Ollama en el PC de Tama.

Mide, por modelo y de a uno (descarga cada modelo de la RAM al terminar): velocidad de
procesamiento de prompt (~1.500 tokens), velocidad de generación, RAM usada y % de llamadas
a herramientas correctas en 10 casos en español.

Uso:
    uv run python bench/models.py qwen3:1.7b qwen3:4b qwen3:8b
    uv run python bench/models.py --write qwen3:4b gemma4:31b-cloud
"""

import argparse
import contextlib
import json
import platform
import statistics
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import psutil

from tama.ollama import OllamaClient, OllamaError

BENCH_DIR = Path(__file__).parent
DEFAULT_MODELS = ["qwen3:1.7b", "qwen3:4b", "qwen3:8b"]
GB = 1024**3

PARAGRAPH = (
    "Renzo vive en Chile, trabaja en proyectos de software y en sus ratos libres imprime "
    "piezas en 3D. Le gusta planificar la semana el domingo por la tarde, anotar ideas "
    "sueltas en el teléfono y revisar sus metas cada mes. Tama lo acompaña en el día a "
    "día: recuerda lo que conversan, propone pequeñas mejoras y avisa de pendientes. "
)


def tool(name: str, description: str, **params: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {p: {"type": "string", "description": d} for p, d in params.items()},
                "required": list(params),
            },
        },
    }


TOOLS = [
    tool("get_time", "Devuelve la fecha y la hora actuales."),
    tool("add_note", "Guarda una nota en las notas de Renzo.", text="Contenido de la nota"),
    tool(
        "set_reminder",
        "Crea un recordatorio para una fecha u hora.",
        text="Qué recordar",
        when="Cuándo, en lenguaje natural",
    ),
    tool("get_weather", "Consulta el clima de una ciudad.", city="Nombre de la ciudad"),
    tool("search_notes", "Busca en las notas guardadas de Renzo.", query="Texto a buscar"),
    tool("calculate", "Evalúa una expresión aritmética.", expression="Expresión, p. ej. 2*3"),
]

TOOL_SYSTEM = (
    "Eres Tama, asistente personal de Renzo. Responde en español. Usa una herramienta "
    "solo cuando la petición lo necesite; si no, responde directamente."
)


@dataclass
class Case:
    prompt: str
    tool: str | None
    arg: str | None = None
    contains: str | None = None


CASES = [
    Case("¿Qué hora es?", "get_time"),
    Case("Anota que mañana compro filamento", "add_note", "text", "filamento"),
    Case("Recuérdame llamar a mi mamá el viernes a las 6", "set_reminder", "text", "mam"),
    Case("¿Va a llover hoy en Santiago?", "get_weather", "city", "santiago"),
    Case("¿Qué había apuntado sobre la impresora 3D?", "search_notes", "query", "impresora"),
    Case("¿Cuánto es el 15% de 2340?", "calculate"),
    Case("Hola Tama, ¿cómo estás?", None),
    Case("Apunta: comprar pilas AA", "add_note", "text", "pilas"),
    Case("Dime la hora, por favor", "get_time"),
    Case("Gracias, eso es todo por hoy", None),
]


@dataclass
class Result:
    model: str
    prompt_tokens: int = 0
    prompt_tps: float = 0.0
    gen_tps: float = 0.0
    load_s: float = 0.0
    ram_gb: float | None = None
    tool_ok: int = 0
    tool_total: int = 0
    tool_avg_s: float = 0.0
    failures: list[str] = field(default_factory=list)
    error: str | None = None


def is_cloud(model: str) -> bool:
    return model.endswith("-cloud") or ":cloud" in model


def long_prompt() -> str:
    # El nonce inicial evita que Ollama reutilice la caché de prompt entre corridas.
    return f"[{uuid.uuid4()}]\n{PARAGRAPH * 16}\nResume en una sola frase lo anterior."


def loaded_ram_gb(client: OllamaClient, model: str) -> float:
    """Memoria que Ollama reporta para el modelo cargado (pesos + caché KV)."""
    return sum(m.get("size", 0) for m in client.running() if m.get("name") == model) / GB


def chat(client: OllamaClient, model: str, messages, think: bool | None, **kw) -> dict:
    try:
        return client.chat(model, messages, think=think, **kw)
    except OllamaError as exc:
        # Modelos sin soporte de "thinking" rechazan el parámetro: se reintenta sin él.
        if think is not None and "think" in str(exc).lower():
            return client.chat(model, messages, think=None, **kw)
        raise


def check_case(case: Case, message: dict[str, Any]) -> str | None:
    """Devuelve None si la respuesta es correcta o un texto con el fallo."""
    calls = message.get("tool_calls") or []
    if case.tool is None:
        return f"llamó {calls[0]['function']['name']}" if calls else None
    if not calls:
        return "no llamó herramienta"
    fn = calls[0]["function"]
    if fn["name"] != case.tool:
        return f"llamó {fn['name']} (esperaba {case.tool})"
    if case.arg:
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            args = json.loads(args or "{}")
        value = str(args.get(case.arg, "")).lower()
        if case.contains not in value:
            return f"{case.arg}={value!r}"
    return None


def bench_model(client: OllamaClient, model: str, reps: int, think: bool | None) -> Result:
    result = Result(model=model)
    # num_gpu 0: solo CPU y RAM, como correrá Tama en este PC.
    options = {"temperature": 0, "num_ctx": 4096, "seed": 42, "num_gpu": 0}
    cloud = is_cloud(model)
    try:
        start = time.perf_counter()
        chat(
            client,
            model,
            [{"role": "user", "content": "hola"}],
            think,
            options={**options, "num_predict": 1},
        )
        result.load_s = time.perf_counter() - start
        if not cloud:
            vram = sum(m.get("size_vram", 0) for m in client.running())
            if vram:
                raise OllamaError(f"el modelo usa {vram / GB:.2f} GB de GPU; debe ser solo CPU")

        prompt_tps, gen_tps = [], []
        for _ in range(reps):
            resp = chat(
                client,
                model,
                [{"role": "user", "content": long_prompt()}],
                think,
                options={**options, "num_predict": 128},
            )
            result.prompt_tokens = resp.get("prompt_eval_count", 0)
            if resp.get("prompt_eval_duration"):
                prompt_tps.append(resp["prompt_eval_count"] / resp["prompt_eval_duration"] * 1e9)
            if resp.get("eval_duration"):
                gen_tps.append(resp["eval_count"] / resp["eval_duration"] * 1e9)
        result.prompt_tps = statistics.median(prompt_tps) if prompt_tps else 0.0
        result.gen_tps = statistics.median(gen_tps) if gen_tps else 0.0
        if not cloud:
            result.ram_gb = loaded_ram_gb(client, model)

        durations = []
        for case in CASES:
            start = time.perf_counter()
            resp = chat(
                client,
                model,
                [
                    {"role": "system", "content": TOOL_SYSTEM},
                    {"role": "user", "content": case.prompt},
                ],
                think,
                tools=TOOLS,
                options={**options, "num_predict": 1024},
            )
            durations.append(time.perf_counter() - start)
            failure = check_case(case, resp.get("message", {}))
            result.tool_total += 1
            if failure is None:
                result.tool_ok += 1
            else:
                result.failures.append(f"{case.prompt} → {failure}")
        result.tool_avg_s = statistics.mean(durations)
    except OllamaError as exc:
        result.error = str(exc)
    finally:
        if not cloud:
            with contextlib.suppress(OllamaError):
                client.unload(model)
    return result


def tps(value: float) -> str:
    # La nube no reporta tiempos de evaluación: "n/d" en vez de un 0 engañoso.
    return f"{value:.1f}" if value else "n/d"


def to_markdown(results: list[Result], ollama_version: str) -> str:
    ram_total = psutil.virtual_memory().total / GB
    lines = [
        "# Resultados del benchmark de modelos",
        "",
        f"- Fecha: {date.today().isoformat()}",
        f"- Equipo: {platform.processor() or platform.machine()}, {ram_total:.0f} GB RAM, "
        f"{platform.system()} {platform.release()}, sin GPU",
        f"- Ollama {ollama_version} nativo; solo CPU (`num_gpu` 0), temperatura 0, "
        "`num_ctx` 4096, thinking apagado",
        f"- Prompt largo: ~{max((r.prompt_tokens for r in results), default=0)} tokens; "
        "herramientas: 10 casos en español (`bench/models.py`)",
        "",
        "| Modelo | Prompt (tok/s) | Generación (tok/s) | Carga (s) | RAM (GB) "
        "| Herramientas | Latencia media herramientas (s) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        if r.error:
            lines.append(f"| {r.model} | error: {r.error} | | | | | |")
            continue
        ram = f"{r.ram_gb:.1f}" if r.ram_gb is not None else "nube"
        pct = 100 * r.tool_ok / r.tool_total if r.tool_total else 0
        lines.append(
            f"| {r.model} | {tps(r.prompt_tps)} | {tps(r.gen_tps)} | {r.load_s:.1f} | {ram} "
            f"| {r.tool_ok}/{r.tool_total} ({pct:.0f}%) | {r.tool_avg_s:.1f} |"
        )
    failures = [(r.model, f) for r in results for f in r.failures]
    if failures:
        lines += ["", "## Fallos de herramientas", ""]
        lines += [f"- `{model}`: {f}" for model, f in failures]
    return "\n".join(lines) + "\n"


def load_previous(models: list[str]) -> list[Result]:
    path = BENCH_DIR / "results.json"
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [Result(**r) for r in raw if r["model"] not in models]


def save(results: list[Result], version: str) -> None:
    (BENCH_DIR / "RESULTS.md").write_text(to_markdown(results, version), encoding="utf-8")
    raw = [asdict(r) for r in results]
    (BENCH_DIR / "results.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("models", nargs="*", default=DEFAULT_MODELS)
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--reps", type=int, default=2, help="repeticiones del prompt largo")
    parser.add_argument("--think", action="store_true", help="activa el razonamiento")
    parser.add_argument("--min-free-gb", type=float, default=8.0, help="RAM libre mínima")
    parser.add_argument("--write", action="store_true", help="escribe bench/RESULTS.md")
    parser.add_argument(
        "--append", action="store_true", help="conserva los resultados previos de otros modelos"
    )
    args = parser.parse_args()

    with OllamaClient(args.host, timeout=900) as client:
        version = client.version()
        installed = set(client.list_models())
        if client.running():
            print("[aviso] hay modelos cargados en Ollama; se miden igual, pero ocupan RAM")
        results = load_previous(args.models) if args.append else []
        for model in args.models:
            if model not in installed:
                print(f"[omitido] {model}: no está instalado (ollama pull {model})")
                continue
            free = psutil.virtual_memory().available / GB
            if not is_cloud(model) and free < args.min_free_gb:
                print(f"[omitido] {model}: solo {free:.1f} GB de RAM libre")
                continue
            print(f"[midiendo] {model} ...", flush=True)
            result = bench_model(client, model, args.reps, think=args.think)
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
            results.append(result)
            if args.write:
                # Se guarda tras cada modelo para no perder resultados si el PC se cae.
                save(results, version)

    print(to_markdown(results, version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
