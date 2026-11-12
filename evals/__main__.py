"""Corre las evals: `uv run python -m evals` (con red) o `uv run python -m evals --offline`."""

import argparse
import sys
import time

import tama  # noqa: F401  (quita el SSLKEYLOGFILE de Avast antes de usar SSL)
from evals import runner
from tama import brain as brain_module
from tama.cli import health_check
from tama.config import get_settings
from tama.net import tcp_probe
from tama.ollama import OllamaClient, OllamaError


def claude_judge(settings):
    """Juez con Claude (decisión de Renzo, 2026-11-12: nunca el modelo local como juez).

    Su gasto se suma al mismo contador que la capa de Claude, así cuenta para el tope.
    """
    import anthropic
    from anthropic import DefaultHttpxClient

    from tama.net import tls_context
    from tama.spend import Spend

    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        max_retries=2,
        http_client=DefaultHttpxClient(verify=tls_context()),
    )
    spend = Spend(
        settings.claude_spend_db or settings.data_dir / "claude_gasto.db",
        settings.claude_usd_per_mtok_in,
        settings.claude_usd_per_mtok_out,
        settings.claude_monthly_usd,
    )
    schema = {
        "type": "object",
        "properties": {
            "puntajes": {"type": "array", "items": {"type": "integer"}},
            "comentario": {"type": "string"},
        },
        "required": ["puntajes", "comentario"],
        "additionalProperties": False,
    }
    used: list[str] = []

    def judge(prompt: str) -> dict:
        if spend.gate():
            raise ValueError(f"juez: {spend.gate()}")
        response = client.messages.create(
            model=settings.claude_model,
            max_tokens=2048,
            system=runner.JUDGE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        )
        spend.add(response.usage)
        text = next((b.text for b in response.content if b.type == "text"), "")
        verdict = runner.parse_json_object(text)
        verdict["puntajes"] = [min(5, max(1, int(n))) for n in verdict.get("puntajes", [])]
        if settings.claude_model not in used:
            used.append(settings.claude_model)
        return verdict

    return judge, used, client.close


def make_judge(settings, local_only: bool = False):
    """Juez de las evals. Con la capa de Claude encendida (--con-claude), solo Claude.

    Si no, en la nube si hay red (vía el Ollama local o con OLLAMA_API_KEY) y, si falla, el modelo
    de fondo local, más lento en CPU. En `--offline` lo que se prueba es la generación sin red:
    el juicio puede usar la nube después.
    """
    if settings.claude_enabled and settings.anthropic_api_key is not None:
        return claude_judge(settings)
    local = OllamaClient(settings.ollama_host, timeout=900)
    local_judge = (
        local,
        settings.background_model,
        True,
        {"num_ctx": 8192, "num_gpu": settings.num_gpu, "num_predict": 8192},
    )
    judges = [local_judge]
    host, _, port = settings.connectivity_probe.rpartition(":")
    if not local_only and tcp_probe(host, int(port)):
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
                verdict = runner.parse_json_object(response["message"]["content"])
                if model not in used:
                    used.append(model)
                return verdict
            except (OllamaError, ValueError, KeyError) as exc:
                error = exc
        raise ValueError(f"ningún juez respondió: {error}")

    def close() -> None:
        for client, *_ in judges:
            client.close()

    return judge, used, close


def rejudge(settings, mode: str, scenarios, local_only: bool, everything: bool = False) -> int:
    """Puntúa de nuevo los escenarios cuyo juicio falló (o todos, con `everything`), sin volver a
    generar las respuestas."""
    judge_name, outcomes = runner.load_mode(mode)
    by_id = {s.id: s for s in scenarios}
    judge, used, close = make_judge(settings, local_only)
    try:
        for outcome in outcomes:
            judge_failed = (outcome.error or "").startswith("juez:")
            not_judged = not outcome.error and not outcome.puntajes
            if not (everything or judge_failed or not_judged) or outcome.scenario not in by_id:
                continue
            if everything and outcome.error and not judge_failed:
                continue  # Tama no respondió: no hay nada que puntuar
            outcome.error = None
            outcome.puntajes = []
            runner.apply_judgement(by_id[outcome.scenario], outcome, judge)
            print(f"[juez] {outcome.scenario}: {outcome.puntajes or outcome.error}", flush=True)
    finally:
        close()
    names = used if everything and used else [judge_name, *used]
    names = [n for n in names if n and n != "pendiente"]
    runner.save_mode(mode, ", ".join(dict.fromkeys(names)), outcomes)
    (runner.EVALS_DIR / "RESULTS.md").write_text(runner.render_report(), encoding="utf-8")
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="solo la capa local, sin red")
    parser.add_argument("--solo", nargs="*", help="ids de escenarios a correr")
    parser.add_argument(
        "--capas", nargs="+", help="usa solo estas capas (nube, claude, local) para medir cada una"
    )
    parser.add_argument("--con-claude", action="store_true", help="incluye la capa de Claude")
    parser.add_argument("--sin-juez", action="store_true", help="no puntuar (solo latencias)")
    parser.add_argument("--juez-local", action="store_true", help="no usar el juez de la nube")
    parser.add_argument(
        "--rejuzgar",
        action="store_true",
        help="puntúa los escenarios guardados que no tienen puntaje o cuyo juicio falló",
    )
    parser.add_argument(
        "--rejuzgar-todos",
        action="store_true",
        help="vuelve a puntuar todos los escenarios guardados (p. ej. con otro juez)",
    )
    args = parser.parse_args()

    settings = get_settings().model_copy(update={"data_dir": runner.EVALS_DIR / "data"})
    scenarios = runner.load_scenarios()
    if args.solo:
        scenarios = [s for s in scenarios if s.id in args.solo]
    mode = "offline" if args.offline else "online"
    if args.capas and not args.offline:
        mode = "online-" + "-".join(args.capas)

    if args.rejuzgar or args.rejuzgar_todos:
        return rejudge(settings, mode, scenarios, args.juez_local, args.rejuzgar_todos)

    # Fase 1: Tama responde. Un turno de calentamiento carga el modelo antes de medir.
    brain = brain_module.build(settings, force_offline=args.offline, only=args.capas)
    problem = health_check(brain.local, settings.ollama_host, settings.local_model)
    if problem:
        brain.close()
        print(f"No se corren las evals: {problem}", file=sys.stderr)
        return 1
    try:
        runner.answer(brain.cascade, [{"role": "user", "content": "hola"}], tools=brain.tools)
        outcomes = []
        for scenario in scenarios:
            start = time.perf_counter()
            outcome = runner.run_scenario(brain.cascade, scenario, brain.tools)
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
        judge, used, close = make_judge(settings, args.juez_local)
        try:
            for scenario, outcome in zip(scenarios, outcomes, strict=True):
                runner.apply_judgement(scenario, outcome, judge)
                print(f"[juez] {scenario.id}: {outcome.puntajes or outcome.error}", flush=True)
                runner.save_mode(mode, ", ".join(used) or "pendiente", outcomes)
        finally:
            close()
        judge_name = ", ".join(used) or "ninguno"

    if args.solo and (runner.EVALS_DIR / f"results-{mode}.json").exists():
        # Con --solo se reemplazan solo esos escenarios en los resultados guardados.
        _, previous = runner.load_mode(mode)
        fresh = {o.scenario: o for o in outcomes}
        outcomes = [fresh.pop(o.scenario, o) for o in previous] + list(fresh.values())
    runner.save_mode(mode, judge_name, outcomes)
    (runner.EVALS_DIR / "RESULTS.md").write_text(runner.render_report(), encoding="utf-8")
    print(runner.render_report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
