"""Evals de Tama: escenarios en `escenarios/*.yaml`, resultados en `RESULTS.md`."""

import os
import sys

# La capa de Claude gasta cupo: solo se mide si se pide con --con-claude.
if "--con-claude" not in sys.argv:
    os.environ["TAMA_CLAUDE_ENABLED"] = "false"
