"""Evals de Tama: escenarios en `escenarios/*.yaml`, resultados en `RESULTS.md`."""

import os
import sys

# La capa de Claude gasta cupo: solo se mide si se pide con --con-claude.
if "--con-claude" not in sys.argv:
    os.environ["TAMA_CLAUDE_ENABLED"] = "false"
# Las evals usan solo la key 3 de Ollama Cloud (decisión de Renzo): no gastan la cuota de las
# keys 1 y 2, que son las del día a día. Con TAMA_EVALS_OLLAMA_KEY=0, todas en orden.
choice = os.environ.get("TAMA_EVALS_OLLAMA_KEY", "3")
if choice.isdigit():
    os.environ["TAMA_CLOUD_KEY_ONLY"] = choice
