"""Corre las evals: `uv run python -m evals` (con red) o `uv run python -m evals --offline`."""

import argparse
import json
import sys
import time

import tama  # noqa: F401  (quita el SSLKEYLOGFILE de Avast antes de usar SSL)
from evals import runner
from tama import brain as brain_module
from tama.config import get_settings
from tama.net import tcp_probe
from tama.ollama import OllamaClient


def make_judge(settings, offline: bool):
    """Juez en la nube si hay clave y red; si no, el modelo de fondo local."""
    host, _, port = settings.connectivity_probe.rpartition(":")
    if settings.ollama_api_key and not offline and tcp_probe(host, int(port)):
        client = OllamaClient(
            settings.cloud_host, timeout=120, api_key=settings.ollama_api_key.get_secret_value()
        )
        model, think, options = settings.cloud_model.removesuffix("-cloud"), None, None
    else:
        client = OllamaClient(settings.ollama_host, timeout=900)
        model, think = settings.background_model, True
        options = {"num_ctx": 8192, "num_gpu": settings.num_gpu, "num_predict": 4096}

    def judge(prompt: str) -> dict:
        response = client.chat(
            model,
            [
                {"role": "system", "content": runner.JUDGE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            think=think,
            options=options,
            format=runner.JUDGE_SCHEMA,
        )
        return json.loads(response["message"]["content"])

    return judge, model, client


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="solo la capa local, sin red")
    parser.add_argument("--solo", nargs="*", help="ids de escenarios a correr")
    parser.add_argument("--sin-juez", action="store_true", help="no puntuar (solo latencias)")
    args = parser.parse_args()

    settings = get_settings().model_copy(update={"data_dir": runner.EVALS_DIR / "data"})
    scenarios = runner.load_scenarios()
    if args.solo:
        scenarios = [s for s in scenarios if s.id in args.solo]
    mode = "offline" if args.offline else "online"

    # Fase 1: Tama responde. Un turno de calentamiento carga el modelo antes de medir.
    brain = brain_module.build(settings, force_offline=args.offline)
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

    # Fase 2: el juez puntúa (después, para no alternar modelos en la RAM).
    judge_name = "ninguno"
    if not args.sin_juez:
        judge, judge_name, client = make_judge(settings, args.offline)
        try:
            for scenario, outcome in zip(scenarios, outcomes, strict=True):
                runner.apply_judgement(scenario, outcome, judge)
                print(f"[juez] {scenario.id}: {outcome.puntajes or outcome.error}", flush=True)
        finally:
            client.close()

    runner.save_mode(mode, judge_name, outcomes)
    (runner.EVALS_DIR / "RESULTS.md").write_text(runner.render_report(), encoding="utf-8")
    print(runner.render_report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
