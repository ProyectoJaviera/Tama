"""Gasto de Claude por mes, contado en el PC (Sprint 11).

Cada llamada a Claude suma su costo con los tokens que informa la API: entrada sin caché, entrada
escrita en caché (~1,25x el precio de entrada), entrada leída de caché (~0,1x) y salida. Un turno
con herramientas hace varias llamadas y todas cuentan. Al llegar al tope, la capa se salta hasta
el mes siguiente; al pasar el 80 % se avisa una vez en la consola.
"""

import sqlite3
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CACHE_WRITE = 1.25
CACHE_READ = 0.10
WARN_AT = 0.80


def month_key(moment: datetime) -> str:
    return moment.strftime("%Y-%m")


def usage_cost(usage: Any, price_in: float, price_out: float) -> float:
    """US$ de una respuesta, desde `response.usage` de la API (precios por millón de tokens)."""

    def tokens(name: str) -> int:
        return int(getattr(usage, name, 0) or 0)

    billed_in = (
        tokens("input_tokens")
        + tokens("cache_creation_input_tokens") * CACHE_WRITE
        + tokens("cache_read_input_tokens") * CACHE_READ
    )
    return (billed_in * price_in + tokens("output_tokens") * price_out) / 1_000_000


class Spend:
    def __init__(
        self,
        path: Path,
        price_in: float,
        price_out: float,
        cap_usd: float,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        say: Callable[[str], None] | None = None,
    ) -> None:
        self.path = path
        self.price_in = price_in
        self.price_out = price_out
        self.cap_usd = cap_usd
        self.clock = clock
        self.say = say or (lambda text: print(f"Claude: {text}", file=sys.stderr, flush=True))
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS spend (month TEXT PRIMARY KEY, usd REAL NOT NULL,"
                " calls INTEGER NOT NULL DEFAULT 0, warned INTEGER NOT NULL DEFAULT 0)"
            )

    def month_usd(self) -> float:
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT usd FROM spend WHERE month = ?", (month_key(self.clock()),)
            ).fetchone()
        return row[0] if row else 0.0

    def add(self, usage: Any) -> float:
        """Suma una respuesta de Claude; devuelve su costo en US$."""
        cost = usage_cost(usage, self.price_in, self.price_out)
        month = month_key(self.clock())
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO spend (month, usd, calls) VALUES (?, ?, 1) ON CONFLICT(month)"
                " DO UPDATE SET usd = usd + excluded.usd, calls = calls + 1",
                (month, cost),
            )
            usd, warned = db.execute(
                "SELECT usd, warned FROM spend WHERE month = ?", (month,)
            ).fetchone()
            if not warned and self.cap_usd > 0 and usd >= self.cap_usd * WARN_AT:
                db.execute("UPDATE spend SET warned = 1 WHERE month = ?", (month,))
                self.say(
                    f"llevas US$ {usd:.2f} de US$ {self.cap_usd:.2f} este mes (80 % del tope)."
                )
        return cost

    def gate(self) -> str | None:
        """Para la cascada: por qué se salta Claude ("" o None si se puede usar)."""
        spent = self.month_usd()
        if spent >= self.cap_usd:
            return f"tope mensual de Claude alcanzado (US$ {spent:.2f} de {self.cap_usd:.2f})"
        return None

    def status(self) -> str:
        spent = self.month_usd()
        share = f" ({spent / self.cap_usd:.0%})" if self.cap_usd > 0 else ""
        return f"US$ {spent:.2f} de US$ {self.cap_usd:.2f} este mes{share}"
