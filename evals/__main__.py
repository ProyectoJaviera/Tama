"""Corre las evals: `uv run python -m evals` (con red) o `uv run python -m evals --offline`."""

import argparse
import json
import sys
import time

import tama  # noqa: F401  (quita el SSLKEYLOGFILE de Avast antes de usar SSL)
from evals import runner
from tama import brain as brain_module
from tama.cli import health_check
from tama.config import get_settings
from tama.net import tcp_probe
from tama.ollama import OllamaClient, OllamaError


def make_judge(settings, offline: bool):
    """Juez en la nube si hay red (vía el Ollama local o con OLLAMA_API_KEY); si no, o si la nube
    falla, el modelo de fondo local."""
    local = OllamaClient(settings.ollama_host, timeout=900)
    local_judge = (
        local,
        settings.background_model,
        True,
        {"num_ctx": 8192, "num_gpu": settings.num_gpu, "num_predict": 8192},
    )
    judges = [local_judge]
    host, _, port = settings.connectivity_probe.rpartition(":")
    if not offline and tcp_probe(host, int(port)):
        if settings.ollama_api_key:
            key = settings.ollama_api_key.get_secret_value()
            cloud = OllamaClient(settings.cloud_host, timeout=120, api_key=key)
            judges.insert(0, (cloud, settings.cloud_model.removesuffix("-cloud"), None, None))
        else:
            judges.insert(0, (local, settings.cloud_model, None, None))
    used: list[str] = []

    def judge(prompt: str) -> dict:
        messages = [
            {"role": "system", "content": runner.JUDGE_SYSTEM},
            {"role": "user", "content": prompt},
        ]
        error: Exception | None = None
        for client, model, think, options in judges:
            try:
                response = client.chat(
                    model, messages, think=think, options=options, format=runner.JUDGE_SCHEMA
                )
                if model not in used:
                    used.append(model)
                return json.loads(response["message"]["content"])
            except (OllamaError, ValueError, KeyError) as exc:
                error = exc
        raise ValueError(f"ningún juez respondió: {error}")

    def close() -> None:
        for client, *_ in judges:
            client.close()

    return judge, used, close


def rejudge(settings, mode: str, scenarios, offline: bool) -> int:
    """Puntúa de nuevo los escenarios cuyo juicio falló, sin volver a generar las respuestas."""
    judge_name, outcomes = runner.load_mode(mode)
    by_id = {s.id: s for s in scenarios}
    judge, used, close = make_judge(settings, offline)
    try:
        for outcome in outcomes:
            if not (outcome.error or "").startswith("juez:") or outcome.scenario not in by_id:
                continue
            outcome.error = None
            runner.apply_judgement(by_id[outcome.scenario], outcome, judge)
            print(f"[juez] {outcome.scenario}: {outcome.puntajes or outcome.error}", flush=True)
    finally:
        close()
    names = [n for n in [judge_name, *used] if n and n != "pendiente"]
    runner.save_mode(mode, ", ".join(dict.fromkeys(names)), outcomes)
    (runner.EVALS_DIR / "RESULTS.md").write_text(runner.render_report(), encoding="utf-8")
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="solo la capa local, sin red")
    parser.add_argument("--solo", nargs="*", help="ids de escenarios a correr")
    parser.add_argument("--sin-juez", action="store_true", help="no puntuar (solo latencias)")
    parser.add_argument(
        "--rejuzgar",
        action="store_true",
        help="vuelve a puntuar solo los escenarios con error del juez en los resultados guardados",
    )
    args = parser.parse_args()

    settings = get_settings().model_copy(update={"data_dir": runner.EVALS_DIR / "data"})
    scenarios = runner.load_scenarios()
    if args.solo:
        scenarios = [s for s in scenarios if s.id in args.solo]
    mode = "offline" if args.offline else "online"

    if args.rejuzgar:
        return rejudge(settings, mode, scenarios, args.offline)

    # Fase 1: Tama responde. Un turno de calentamiento carga el modelo antes de medir.
    brain = brain_module.build(settings, force_offline=args.offline)
    problem = health_check(brain.local, settings.ollama_host, settings.local_model)
    if problem:
        brain.close()
        print(f"No se corren las evals: {problem}", file=sys.stderr)
        return 1
    try:
        runner.answer(brain.cascade, [{"role": "user", "content": "hola"}])
        outcomes = []
        for scenario in scenarios:
            start = time.perf_counter()
            outcome = runner.run_scenario(brain.cascade, scenario)
            outcomes.append(outcome)
            print(
                f"[{mode}] {scenario.id}: {outcome.capa or 'error'} "
                f"({time.perf_counter() - start:.0f} s)",
                flush=True,
            )
    finally:
        brain.close()
    # Se guarda tras cada fase y cada escenario juzgado: si el proceso se corta, no se pierde todo.
    runner.save_mode(mode, "pendiente", outcomes)

    # Fase 2: el juez puntúa (después, para no alternar modelos en la RAM).
    judge_name = "ninguno"
    if not args.sin_juez:
        judge, used, close = make_judge(settings, args.offline)
        try:
            for scenario, outcome in zip(scenarios, outcomes, strict=True):
                runner.apply_judgement(scenario, outcome, judge)
                print(f"[juez] {scenario.id}: {outcome.puntajes or outcome.error}", flush=True)
                runner.save_mode(mode, ", ".join(used) or "pendiente", outcomes)
        finally:
            close()
        judge_name = ", ".join(used) or "ninguno"

    runner.save_mode(mode, judge_name, outcomes)
    (runner.EVALS_DIR / "RESULTS.md").write_text(runner.render_report(), encoding="utf-8")
    print(runner.render_report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
