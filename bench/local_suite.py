"""Banco del modelo local: todas las evals del sprint final, sin red, un modelo a la vez.

Por cada modelo corre las evals con `TAMA_LOCAL_MODEL` apuntando a él, guarda los registros en
`bench/local/<modelo>/` y arma `bench/LOCAL_RESULTS.md`. El juez es la nube, nunca Claude. Los
resultados de la nube que están en `evals/` se restauran al terminar cada eval.

Uso:
    uv run python bench/local_suite.py qwen3:4b-instruct-2507-q4_K_M qwen3:8b
    uv run python bench/local_suite.py --solo-rendimiento gemma4:31b gemma2:2b
    uv run python bench/local_suite.py --resumen          # solo rehace la tabla
"""

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bench.models import GB, bench_model  # noqa: E402
from tama.ollama import OllamaClient  # noqa: E402

OUT = ROOT / "bench" / "local"
RAM_LIMIT_GB = 28
EVALS = ROOT / "evals"
SHARED = [
    "memoria-results.json",
    "herramientas-results.json",
    "reflexion-results.json",
    "results-offline.json",
    "RESULTS.md",
    "RESULTS_MEMORIA.md",
    "RESULTS_HERRAMIENTAS.md",
    "RESULTS_REFLEXION.md",
]
# (nombre, argumentos de python -m, horas máximas)
SUITE = [
    ("memoria", ["evals.memoria", "--capas", "local", "--offline"], 3),
    ("correccion", ["evals.memoria", "--capas", "local", "--offline", "--correccion"], 1),
    ("historial", ["evals.memoria", "--capas", "local", "--offline", "--historial"], 1),
    ("rectificacion", ["evals.memoria", "--capas", "local", "--offline", "--rectificacion"], 2),
    ("objetivos", ["evals.reflexion", "objetivos", "--capas", "local", "--offline"], 1),
    ("herramientas", ["evals.tools", "--capas", "local", "--offline"], 3),
    ("avisos", ["evals.avisos", "--capas", "local", "--offline"], 2),
    ("conversacion", ["evals", "--offline"], 3),
    ("reflexion", ["evals.reflexion", "reflexion"], 2),
    ("diario", ["evals.reflexion", "diario"], 1),
    ("sin_red", ["evals.smoke"], 1),
]


def folder(model: str) -> Path:
    return OUT / re.sub(r"[^\w.-]", "_", model)


class RamWatch:
    """Pico de RAM ocupada en el PC mientras corre una eval (incluye el modelo y Tama)."""

    def __init__(self) -> None:
        self.peak = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(2):
            self.peak = max(self.peak, psutil.virtual_memory().used / GB)

    def __enter__(self) -> "RamWatch":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join()


def guard_cpu(client: OllamaClient) -> None:
    """Corta el banco si algún modelo cargado usa GPU."""
    vram = sum(m.get("size_vram", 0) for m in client.running())
    if vram:
        raise SystemExit(f"Un modelo usa {vram / GB:.2f} GB de GPU: el banco se detiene.")


def run_eval(
    model: str, name: str, args: list[str], hours: float, client: OllamaClient, tag: str = ""
) -> dict:
    saved = {f: (EVALS / f).read_bytes() for f in SHARED if (EVALS / f).exists()}
    env = {
        **os.environ,
        "TAMA_LOCAL_MODEL": model,
        "TAMA_BACKGROUND_MODEL": model,
        "TAMA_CLAUDE_ENABLED": "false",
        "TAMA_NUM_GPU": "0",
        "PYTHONIOENCODING": "utf-8",
    }
    log = folder(model) / f"{name}{tag}.log"
    start = time.perf_counter()
    status = "ok"
    with RamWatch() as ram, log.open("w", encoding="utf-8") as out:
        proc = subprocess.Popen(
            [sys.executable, "-m", *args], cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT
        )
        deadline = time.monotonic() + hours * 3600
        while proc.poll() is None:
            time.sleep(2)
            if psutil.virtual_memory().used / GB > RAM_LIMIT_GB:
                # Con 32 GB, pasar de ~28 arriesga a Windows: se corta esta eval.
                proc.kill()
                status = f"RAM sobre {RAM_LIMIT_GB} GB"
            elif time.monotonic() > deadline:
                proc.kill()
                status = "tiempo agotado"
        # Las evals salen con 1 cuando algún caso falla: eso es un resultado, no un error.
        if status == "ok" and proc.returncode not in (0, 1):
            status = f"salió con {proc.returncode}"
    guard_cpu(client)
    fresh = {}
    for f in (
        "memoria-results.json",
        "herramientas-results.json",
        "reflexion-results.json",
        "results-offline.json",
    ):
        with contextlib.suppress(OSError, ValueError):
            fresh[f] = json.loads((EVALS / f).read_text(encoding="utf-8"))
    result = {
        "estado": status,
        "minutos": round((time.perf_counter() - start) / 60, 1),
        "ram_pico_gb": round(ram.peak, 1),
        **extract(name, log.read_text(encoding="utf-8", errors="replace"), fresh),
    }
    for f, data in saved.items():
        # Lo del modelo local queda en bench/local; evals/ vuelve a los resultados de la nube.
        (EVALS / f).write_bytes(data)
    return result


def checks(item: dict[str, Any], skip: tuple[str, ...] = ("capa", "sin_red")) -> str:
    """Cuántas comprobaciones verdaderas tiene un resultado: "4/5"."""
    flags = [v for k, v in item.items() if k not in skip and isinstance(v, bool)]
    return f"{sum(flags)}/{len(flags)}" if flags else "-"


def extract(name: str, text: str, fresh: dict[str, Any]) -> dict[str, Any]:
    """Los números de cada eval: de su JSON si lo escribe, si no, de su salida."""
    memory = fresh.get("memoria-results.json", {})
    reflection = fresh.get("reflexion-results.json", {})
    found: dict[str, Any] = {"abre_grupos": text.count("abrir_herramientas")}
    if name == "memoria" and "local (sin red)" in memory:
        r = memory["local (sin red)"]
        found.update(
            hechos=f"{r['hechos']}/{r['total_hechos']}",
            negativos=f"{r['negativos']}/{r['total_negativos']}",
            tokens_prompt_max=r["tokens_prompt_max"],
        )
    elif name == "correccion" and "corrección local (sin red)" in memory:
        found["checks"] = checks(memory["corrección local (sin red)"])
    elif name == "historial" and "historial local (sin red)" in memory:
        found["checks"] = checks(memory["historial local (sin red)"])
    elif name == "rectificacion":
        match = re.findall(r"^local: (\d+/\d+)", text, re.MULTILINE)
        found["correctas"] = match[-1] if match else "-"
    elif name == "objetivos" and "objetivos local (sin red)" in reflection:
        item = reflection["objetivos local (sin red)"]["checks"]
        values = list(item.values()) if isinstance(item, dict) else item
        flags = [bool(v.get("ok", v)) if isinstance(v, dict) else bool(v) for v in values]
        found["checks"] = f"{sum(flags)}/{len(flags)}"
    elif name == "herramientas":
        r = fresh.get("herramientas-results.json", {}).get("local (sin red)")
        if r:
            found.update(
                correctos=f"{r['correctos']}/{r['casos']}",
                pct=f"{r['pct']}%",
                ttft_p50=r["ttft_p50"],
                voseo=r["voseo"],
            )
    elif name == "avisos":
        match = re.findall(r"^Recordatorios: (\d+/\d+)", text, re.MULTILINE)
        found["recordatorios"] = match[-1] if match else "-"
    elif name == "conversacion" and "results-offline.json" in fresh:
        r = fresh["results-offline.json"]["resumen"]
        found.update(
            puntaje_medio=r["puntaje_medio"],
            aprobados=f"{r['aprobados']}/{r['escenarios']}",
            errores=r["errores"],
            ttft_p50=r["ttft_p50"],
            total_p50=r["total_p50"],
        )
    elif name == "reflexion" and "reflexion" in reflection:
        r = reflection["reflexion"]
        found.update(
            duplicados=f"{len(r['fusionados'])}/{r['total_duplicados']}",
            fusiones_incorrectas=r["fusiones_incorrectas"],
            contradicciones=f"{len(r['contradicciones'])}/{r['total_contradicciones']}",
        )
    elif name == "diario" and "diario" in reflection:
        found["juez"] = reflection["diario"].get("puntajes")
    elif name == "sin_red":
        match = re.findall(r"^(\d+/\d+) casos", text, re.MULTILINE)
        found["casos"] = match[-1] if match else "-"
    return found


def performance(client: OllamaClient, model: str) -> dict[str, Any]:
    result = bench_model(client, model, reps=2, think=False)
    return {
        "carga_s": round(result.load_s, 1),
        "prompt_tps": round(result.prompt_tps, 1),
        "gen_tps": round(result.gen_tps, 1),
        "ram_gb": round(result.ram_gb or 0, 1),
        "herramientas_10": f"{result.tool_ok}/{result.tool_total}",
        "error": result.error,
    }


def summary_path(model: str, round_no: int = 1) -> Path:
    """La ronda 1 va a la tabla; las repeticiones, aparte, para ver la media y el rango."""
    return folder(model) / ("resumen.json" if round_no == 1 else f"resumen-r{round_no}.json")


def run_model(
    client: OllamaClient,
    model: str,
    only_perf: bool,
    names: list[str] | None,
    round_no: int = 1,
) -> None:
    folder(model).mkdir(parents=True, exist_ok=True)
    path = summary_path(model, round_no)
    tag = "" if round_no == 1 else f"-r{round_no}"
    summary = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"modelo": model}

    def save() -> None:
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_table()

    tags = client._request("GET", "/api/tags").get("models", [])
    size = next((m.get("size", 0) for m in tags if m["name"] == model), 0) / GB
    free = psutil.virtual_memory().available / GB
    if free < size + 4:
        print(f"[omitido] {model}: necesita {size + 4:.1f} GB libres y hay {free:.1f}", flush=True)
        return
    if round_no == 1:
        print(f"[{datetime.now():%H:%M}] {model}: rendimiento", flush=True)
        summary["rendimiento"] = performance(client, model)
        if summary["rendimiento"]["error"]:
            save()
            print(f"  error: {summary['rendimiento']['error']}", flush=True)
            return
        save()
    if only_perf:
        return
    for name, args, hours in SUITE:
        if names and name not in names:
            continue
        print(f"[{datetime.now():%H:%M}] {model}: {name}{tag}", flush=True)
        summary[name] = run_eval(model, name, args, hours, client, tag)
        print(f"  {summary[name]}", flush=True)
        save()
    client.unload(model)


COLUMNS = [
    ("Memoria", "memoria", "hechos"),
    ("Negativos", "memoria", "negativos"),
    ("Tokens máx", "memoria", "tokens_prompt_max"),
    ("Corrección", "correccion", "checks"),
    ("Historial", "historial", "checks"),
    ("Objetivos", "objetivos", "checks"),
    ("Rectificación", "rectificacion", "correctas"),
    ("Herramientas", "herramientas", "correctos"),
    ("Abre grupos", "herramientas", "abre_grupos"),
    ("Recordatorios", "avisos", "recordatorios"),
    ("Conversación", "conversacion", "puntaje_medio"),
    ("1er token p50", "conversacion", "ttft_p50"),
    ("Reflexión", "reflexion", "duplicados"),
    ("Fusiones malas", "reflexion", "fusiones_incorrectas"),
    ("Contradicciones", "reflexion", "contradicciones"),
    ("Diario", "diario", "juez"),
    ("Sin red", "sin_red", "casos"),
]


def write_table() -> None:
    rows = []
    for path in sorted(OUT.glob("*/resumen.json")):
        rows.append(json.loads(path.read_text(encoding="utf-8")))
    lines = [
        "# Banco del modelo local",
        "",
        "Generado por `bench/local_suite.py`: CPU sola (num_gpu 0), un modelo a la vez, sin red;",
        "juez en la nube sin Claude. Registros en `bench/local/<modelo>/`.",
        "",
        "## Rendimiento",
        "",
        "| Modelo | Carga | Prompt tok/s | Generación tok/s | RAM GB | Herramientas (10) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        p = r.get("rendimiento", {})
        lines.append(
            f"| {r['modelo']} | {p.get('carga_s', '-')} s | {p.get('prompt_tps', '-')} | "
            f"{p.get('gen_tps', '-')} | {p.get('ram_gb', '-')} | {p.get('herramientas_10', '-')} |"
        )
    lines += ["", "## Evals sin red", ""]
    lines.append("| Modelo | " + " | ".join(c[0] for c in COLUMNS) + " | Horas |")
    lines.append("|---|" + "---:|" * (len(COLUMNS) + 1))
    for r in rows:
        if not any(name in r for name, *_ in SUITE):
            continue
        cells = []
        for _, name, key in COLUMNS:
            item = r.get(name, {})
            value = item.get(key, "-") if item.get("estado", "ok") == "ok" else item["estado"]
            if isinstance(value, list) and key == "fusiones_incorrectas":
                value = len(value)
            cells.append(str(value))
        hours = sum(r.get(name, {}).get("minutos", 0) for name, *_ in SUITE) / 60
        lines.append(f"| {r['modelo']} | " + " | ".join(cells) + f" | {hours:.1f} |")
    (ROOT / "bench" / "LOCAL_RESULTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("models", nargs="*")
    parser.add_argument("--solo-rendimiento", action="store_true")
    parser.add_argument("--evals", nargs="+", help="solo estas evals del banco")
    parser.add_argument("--resumen", action="store_true", help="solo rehace la tabla")
    parser.add_argument("--ronda", type=int, default=1, help="repetición (2, 3...) aparte")
    args = parser.parse_args()
    if args.resumen:
        write_table()
        return 0
    with OllamaClient("http://127.0.0.1:11434", timeout=900) as client:
        guard_cpu(client)
        for model in args.models:
            run_model(client, model, args.solo_rendimiento, args.evals, args.ronda)
    write_table()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
