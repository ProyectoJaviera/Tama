"""Genera los íconos de la PWA en src/tama/web/static/ (PNG, sin dependencias).

Un huevo claro con dos ojos sobre el verde de Tama. Se corre una vez y los PNG se versionan:

    uv run python scripts/make_icons.py
"""

import struct
import zlib
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "src" / "tama" / "web" / "static"
GREEN = (47, 107, 94)
EGG = (246, 245, 241)
EYE = (36, 35, 31)


def png(width: int, rows: list[bytes]) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + row for row in rows)
    header = struct.pack(">IIBBBBB", width, len(rows), 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def inside_round_square(x: float, y: float, radius: float) -> bool:
    """Cuadrado de lado 1 con esquinas redondeadas; x, y entre 0 y 1."""
    cx = min(max(x, radius), 1 - radius)
    cy = min(max(y, radius), 1 - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius**2


def color_at(x: float, y: float, maskable: bool) -> tuple[int, int, int, int]:
    # El ícono "maskable" llena todo el cuadro y deja el dibujo en la zona segura (80 % central).
    scale = 0.62 if maskable else 0.78
    u, v = (x - 0.5) / scale + 0.5, (y - 0.5) / scale + 0.5
    if not maskable and not inside_round_square(x, y, 0.22):
        return (0, 0, 0, 0)
    # Huevo: elipse algo más ancha abajo.
    width = 0.30 + 0.05 * max(0.0, v - 0.45) / 0.55
    egg = ((u - 0.5) / width) ** 2 + ((v - 0.54) / 0.40) ** 2 <= 1
    eyes = any((u - ex) ** 2 + (v - 0.52) ** 2 <= 0.045**2 for ex in (0.41, 0.59))
    if egg and eyes:
        return (*EYE, 255)
    if egg:
        return (*EGG, 255)
    return (*GREEN, 255)


def icon(size: int, maskable: bool = False) -> bytes:
    rows = []
    for py in range(size):
        row = bytearray()
        for px in range(size):
            # 4 muestras por pixel para que los bordes no queden dentados.
            samples = [
                color_at((px + dx) / size, (py + dy) / size, maskable)
                for dx in (0.25, 0.75)
                for dy in (0.25, 0.75)
            ]
            row += bytes(sum(s[i] for s in samples) // 4 for i in range(4))
        rows.append(bytes(row))
    return png(size, rows)


def main() -> None:
    outputs = {
        "icono-192.png": icon(192),
        "icono-512.png": icon(512),
        "icono-maskable-512.png": icon(512, maskable=True),
        "icono-apple-180.png": icon(180, maskable=True),
    }
    for name, data in outputs.items():
        (STATIC / name).write_bytes(data)
        print(f"{name}: {len(data)} bytes")


if __name__ == "__main__":
    main()
