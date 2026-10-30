"""Copia en vendor/ las wheels de todas las dependencias fijadas en uv.lock (con red, una vez).

Así Tama y sus servidores MCP se pueden reinstalar sin internet:

    uv run python scripts/vendor.py            # con red: llena vendor/wheels
    uv run python scripts/vendor.py --docker   # igual, pero descargando desde un contenedor
                                               # (si un antivirus corrompe las descargas de pip)

    # sin red: reinstalar desde las wheels
    uv venv && uv pip install --offline --no-index --find-links vendor/wheels \\
        -r vendor/requirements-windows.txt && \
        uv pip install --offline --no-index --find-links vendor/wheels --no-deps .

Descarga para Windows (este PC) y para Linux x86_64 (la imagen Docker), Python 3.12. Las
wheels no se versionan en git (pesan decenas de MB); los requirements por plataforma sí.
"""

import argparse
import subprocess
import sys
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor"
WHEELS = VENDOR / "wheels"
IMAGE = "tama:0.1.0"

# Entorno de cada plataforma para evaluar los marcadores (sys_platform == 'win32', etc.).
PLATFORMS = {
    "windows": (
        "win_amd64",
        {"sys_platform": "win32", "os_name": "nt", "platform_system": "Windows"},
    ),
    "linux": (
        "manylinux2014_x86_64",
        {"sys_platform": "linux", "os_name": "posix", "platform_system": "Linux"},
    ),
}
COMMON_ENV = {
    "python_version": "3.12",
    "python_full_version": "3.12.13",
    "implementation_name": "cpython",
    "platform_python_implementation": "CPython",
    "platform_machine": "x86_64",
}


def run(cmd: list[str]) -> None:
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)


def export_requirements() -> list[str]:
    target = VENDOR / "requirements.txt"
    run(
        [
            "uv",
            "export",
            "--format",
            "requirements.txt",
            "--no-dev",
            "--no-group",
            "bench",
            "--no-emit-project",
            "--no-hashes",
            "--output-file",
            str(target),
        ]
    )
    lines = target.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def for_platform(lines: list[str], env: dict[str, str]) -> list[str]:
    """Versiones exactas que aplican en la plataforma, sin marcadores."""
    pinned = []
    for line in lines:
        req = Requirement(line)
        if req.marker is None or req.marker.evaluate({**COMMON_ENV, **env}):
            pinned.append(f"{req.name}{req.specifier}")
    return pinned


def download(requirements: Path, platform: str, docker: bool) -> None:
    args = [
        "pip",
        "download",
        "--no-deps",
        "--only-binary",
        ":all:",
        "--platform",
        platform,
        "--python-version",
        "3.12",
        "--implementation",
        "cp",
        "--retries",
        "5",
    ]
    if docker:
        run(
            [
                "docker",
                "run",
                "--rm",
                "--user",
                "0",
                "--entrypoint",
                "uv",
                "-e",
                "HOME=/tmp",
                "-v",
                f"{VENDOR}:/vendor",
                IMAGE,
                "tool",
                "run",
                *args,
                "--requirement",
                f"/vendor/{requirements.name}",
                "--dest",
                "/vendor/wheels",
            ]
        )
    else:
        # pip se usa solo aquí, al instalar; Tama nunca descarga nada al arrancar.
        run(["uv", "tool", "run", *args, "--requirement", str(requirements), "--dest", str(WHEELS)])


def download_build_backend(docker: bool) -> None:
    """El backend de construcción (hatchling, puro Python) para instalar Tama misma sin red."""
    build = VENDOR / "requirements-build.txt"
    build.write_text("hatchling>=1.25\n", encoding="utf-8")
    args = ["pip", "download", "--only-binary", ":all:", "--retries", "5"]
    if docker:
        run(
            [
                "docker",
                "run",
                "--rm",
                "--user",
                "0",
                "--entrypoint",
                "uv",
                "-e",
                "HOME=/tmp",
                "-v",
                f"{VENDOR}:/vendor",
                IMAGE,
                "tool",
                "run",
                *args,
                "--requirement",
                f"/vendor/{build.name}",
                "--dest",
                "/vendor/wheels",
            ]
        )
    else:
        run(["uv", "tool", "run", *args, "--requirement", str(build), "--dest", str(WHEELS)])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--docker", action="store_true", help="descargar desde un contenedor")
    args = parser.parse_args()

    WHEELS.mkdir(parents=True, exist_ok=True)
    lines = export_requirements()
    for name, (platform, env) in PLATFORMS.items():
        requirements = VENDOR / f"requirements-{name}.txt"
        requirements.write_text("\n".join(for_platform(lines, env)) + "\n", encoding="utf-8")
        download(requirements, platform, args.docker)
    download_build_backend(args.docker)
    wheels = list(WHEELS.glob("*.whl"))
    size = sum(p.stat().st_size for p in wheels) / 1024**2
    print(f"Listo: {len(wheels)} wheels ({size:.0f} MB) en {WHEELS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
