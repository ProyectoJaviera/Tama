"""Evals del Sprint 12 (sentidos): describir una foto de taller y resumir un PDF de 20 páginas.

    uv run python -m evals.sentidos --capas claude --con-claude

Datos ficticios, en carpetas temporales. Tama responde con la capa pedida y las herramientas de
adjuntos; un juez Claude puntúa (nunca el modelo local). Además se revisa, sin juez, que el resumen
nombre los datos clave del PDF y que la descripción hable del taller.
"""

import argparse
import io
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import tama  # noqa: F401  (quita el SSLKEYLOGFILE de Avast)
from evals import runner
from evals.__main__ import make_judge
from evals.memoria import ask
from tama import brain as brain_module
from tama.attachments import Attachments, AttachmentStore, AttachmentTools, Senses
from tama.config import get_settings
from tama.mcp_servers.memoria import normalize
from tama.memory import session_prompt
from tests.pdf_helper import make_pdf

EVALS_DIR = Path(__file__).parent
RESULTS = EVALS_DIR / "sentidos-results.json"

# Manual ficticio de 20 páginas; los datos clave van repartidos en páginas distintas.
KEY_FACTS = {
    "aceite": (3, "El aceite del motor se cambia cada 10.000 km o una vez al año."),
    "neumaticos": (7, "La presión de los neumáticos es de 32 psi adelante y 30 psi atrás."),
    "frenos": (11, "Las pastillas de freno se revisan cada 20.000 km."),
    "garantia": (15, "La garantía del taller dura 12 meses en repuestos y mano de obra."),
    "pago": (19, "El pago se hace en 30 días desde la entrega del vehículo."),
}
FILLER = (
    "Este capítulo describe el trabajo diario del taller, el orden de las herramientas y la "
    "limpieza del puesto. Cada técnico registra lo que hizo en la ficha del vehículo."
)


def manual_pdf() -> bytes:
    pages = []
    for number in range(1, 21):
        lines = [f"Manual de mantención del Taller Flores - página {number}", ""]
        lines += [FILLER[i : i + 90] for i in range(0, len(FILLER), 90)]
        for page, fact in KEY_FACTS.values():
            if page == number:
                lines += ["", fact]
        pages.append(lines)
    return make_pdf(pages)


def workshop_photo() -> bytes:
    """Un tablero de herramientas dibujado (con rótulos), para no usar fotos de nadie."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (1200, 800), (196, 170, 130))
    d = ImageDraw.Draw(img)
    d.rectangle([60, 60, 1140, 520], fill=(70, 90, 70))  # tablero perforado
    for x in range(80, 1140, 30):
        for y in range(80, 520, 30):
            d.ellipse([x, y, x + 5, y + 5], fill=(40, 50, 40))
    d.rectangle([150, 120, 180, 420], fill=(150, 150, 160))  # llave
    d.ellipse([130, 90, 200, 150], outline=(150, 150, 160), width=12)
    d.rectangle([330, 150, 360, 450], fill=(120, 70, 30))  # martillo: mango
    d.rectangle([290, 120, 400, 170], fill=(80, 80, 90))
    d.rectangle([520, 140, 545, 430], fill=(200, 30, 30))  # destornillador
    d.polygon([(520, 430), (545, 430), (532, 470)], fill=(170, 170, 170))
    d.rectangle([0, 560, 1200, 800], fill=(110, 80, 50))  # banco de trabajo
    d.rectangle([700, 600, 1000, 700], fill=(60, 60, 200))  # caja de herramientas
    for x, text in ((120, "LLAVE"), (300, "MARTILLO"), (480, "DESTORNILLADOR"), (720, "CAJA")):
        d.text((x, 470 if x < 700 else 710), text, fill=(255, 255, 255))
    d.text((60, 20), "TALLER FLORES - BANCO DE TRABAJO", fill=(20, 20, 20))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90)
    return out.getvalue()


BODEGA_FACTS = [
    "La bodega abre de 9 a 18 horas de lunes a viernes.",
    "El inventario de repuestos se hace el primer lunes de cada mes.",
    "Los aceites se guardan en el estante C, lejos del calor.",
]


def bodega_pdf() -> bytes:
    pages = []
    for number in range(1, 6):
        lines = [f"Reglamento de la bodega del Taller Flores - página {number}", "", FILLER[:90]]
        if number <= len(BODEGA_FACTS):
            lines += ["", BODEGA_FACTS[number - 1]]
        pages.append(lines)
    return make_pdf(pages)


CASES = [
    {
        "id": "foto-taller",
        "archivo": ("taller.jpg", "image/jpeg", workshop_photo),
        "mensaje": "Te mando una foto de mi taller, ¿qué ves?",
        "criterios": [
            "Describe un taller o banco de trabajo con herramientas",
            "Nombra al menos dos herramientas que se ven (llave, martillo, destornillador, caja)",
            "No inventa: la foto (que el juez no ve) tiene un tablero perforado verde con una "
            "llave, un martillo y un destornillador de mango rojo rotulados, un banco de madera, "
            "una caja azul rotulada CAJA y el título TALLER FLORES - BANCO DE TRABAJO; nada más",
        ],
        "palabras": ["herramienta", "martillo", "destornillador", "llave", "banco", "taller"],
        "herramienta": "ver_imagen",
    },
    {
        "id": "pdf-20-paginas",
        "archivo": ("manual-taller.pdf", "application/pdf", manual_pdf),
        "mensaje": "Te adjunto el manual del taller, resúmemelo.",
        "criterios": [
            "Resume el documento en puntos claros y breves",
            "Incluye los datos clave: aceite cada 10.000 km, neumáticos 32/30 psi, frenos cada "
            "20.000 km, garantía de 12 meses y pago a 30 días",
            "No inventa: el manual (que el juez no ve) tiene 20 páginas sobre el trabajo diario "
            "del taller, el orden y la limpieza, la ficha del vehículo y esos cinco datos (en las "
            "páginas 3, 7, 11, 15 y 19); nada "
            "más. Tama lo resumió con la herramienta resumir_documento",
        ],
        "palabras": ["10.000", "32", "20.000", "12 meses", "30 días"],
        "herramienta": "resumir_documento",
    },
    {
        "id": "varios-adjuntos",
        "archivos": [
            ("manual-taller.pdf", "application/pdf", manual_pdf),
            ("reglamento-bodega.pdf", "application/pdf", bodega_pdf),
            ("taller.jpg", "image/jpeg", workshop_photo),
        ],
        "mensaje": "Te mando los dos documentos del taller y una foto: resúmeme los dos y "
        "dime qué hay en la foto.",
        "criterios": [
            "Resume los dos documentos por separado o claramente distinguidos",
            "Del manual incluye datos clave (aceite cada 10.000 km, garantía 12 meses, pago a 30 "
            "días); del reglamento, horario 9 a 18, inventario el primer lunes y aceites en el "
            "estante C",
            "Describe la foto: un tablero con llave, martillo y destornillador, banco y caja azul",
            "No inventa: los documentos y la foto (que el juez no ve) solo tienen eso, los otros "
            "datos del manual (neumáticos, frenos) y texto general sobre el trabajo del taller",
        ],
        "palabras": ["10.000", "12 meses", "9 a 18", "primer lunes", "estante c", "martillo"],
        "herramienta": "resumir_documento,ver_imagen",
    },
    {
        "id": "seis-adjuntos",
        "archivos": [
            ("tablero-taller.jpg", "image/jpeg", workshop_photo),
            ("cielo-azul.png", "image/png", lambda: drawing("cielo")),
            ("semaforo.jpg", "image/jpeg", lambda: drawing("semaforo")),
            ("casa-roja.png", "image/png", lambda: drawing("casa")),
            ("manual-taller.pdf", "application/pdf", manual_pdf),
            ("lista-compras.txt", "text/plain", lambda: b"Pan, leche, huevos y cafe."),
        ],
        "mensaje": "¿Puedes describirme los adjuntos?",
        "criterios": [
            "Describe los seis adjuntos, cada uno por separado",
            "Nombra cada adjunto por su nombre de archivo (tablero-taller.jpg, cielo-azul.png, "
            "semaforo.jpg, casa-roja.png, manual-taller.pdf, lista-compras.txt) y nunca por un "
            "código interno como a3",
            "No inventa: son un tablero de herramientas, un cielo azul con sol, un semáforo, una "
            "casa roja (dibujos), un manual de mantención y una lista de compras",
        ],
        "palabras": [
            "tablero-taller.jpg",
            "cielo-azul.png",
            "semaforo.jpg",
            "casa-roja.png",
            "manual-taller.pdf",
            "lista-compras.txt",
        ],
        "herramienta": "ver_imagen",
    },
]


def drawing(what: str) -> bytes:
    """Dibujos simples y reconocibles para la eval de varios adjuntos."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (800, 600), (255, 255, 255))
    d = ImageDraw.Draw(img)
    if what == "cielo":
        d.rectangle([0, 0, 800, 600], fill=(80, 150, 240))
        d.ellipse([560, 60, 720, 220], fill=(255, 220, 40))
        d.text((40, 540), "CIELO AZUL", fill=(255, 255, 255))
    elif what == "semaforo":
        d.rectangle([330, 80, 470, 520], fill=(40, 40, 40))
        for y, color in ((100, (220, 30, 30)), (240, (240, 200, 30)), (380, (30, 190, 60))):
            d.ellipse([350, y, 450, y + 100], fill=color)
        d.text((330, 540), "SEMAFORO", fill=(0, 0, 0))
    else:
        d.rectangle([250, 280, 550, 520], fill=(200, 40, 40))
        d.polygon([(220, 280), (400, 120), (580, 280)], fill=(120, 60, 30))
        d.rectangle([370, 400, 430, 520], fill=(90, 50, 20))
        d.text((330, 540), "CASA ROJA", fill=(0, 0, 0))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def run_case(case: dict[str, Any], layer: str, offline: bool) -> dict[str, Any]:
    base = Path(tempfile.mkdtemp(prefix=f"tama-sentidos-{layer}-"))
    settings = get_settings().model_copy(
        update={
            "data_dir": base / "data",
            "workdir": base / "trabajo",
            "memory_dir": base / "memoria",
            "connectivity_watch_s": 0,
        }
    )
    brain = brain_module.build(settings, force_offline=offline, only=[layer])
    store = AttachmentStore(settings.data_dir)
    online = brain.connectivity.online if brain.connectivity else (lambda: True)
    attachments = Attachments(store, Senses(settings, brain.spend, online))
    files = case.get("archivos") or [case["archivo"]]
    refs = [f"a{store.save(name, kind, make())['id']}" for name, kind, make in files]
    text = f"{case['mensaje']}\n\n{attachments.note(refs)}"
    brain.tools = AttachmentTools(brain.tools, attachments)
    messages = [{"role": "system", "content": session_prompt(settings.memory_dir, brain.store)}]
    try:
        turn = ask(brain, messages, text, settings.history_max_tokens, settings.tools_max)
    finally:
        brain.close()
    answer = turn["respuesta"]
    plain = normalize(answer)
    found = [w for w in case["palabras"] if normalize(w) in plain]
    return {
        "caso": case["id"],
        "capa": layer,
        "herramientas": turn["herramientas"],
        "uso_herramienta": all(h in turn["herramientas"] for h in case["herramienta"].split(",")),
        "palabras": f"{len(found)}/{len(case['palabras'])}",
        # Los ids son internos: con Renzo, los adjuntos se nombran por su archivo.
        "ids_a_la_vista": sorted(set(re.findall(r"\ba\d+\b", answer))),
        "llamadas_vision": turn["herramientas"].count("ver_imagen"),
        "respuesta": answer,
    }


def judge_case(case: dict[str, Any], result: dict[str, Any], judge) -> None:
    scenario = runner.Scenario(
        id=case["id"], descripcion=case["id"], mensaje=case["mensaje"], criterios=case["criterios"]
    )
    outcome = runner.Outcome(scenario=case["id"], respuesta=result["respuesta"])
    runner.apply_judgement(scenario, outcome, judge)
    result["puntajes"] = outcome.puntajes
    result["comentario"] = outcome.comentario or outcome.error


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capas", nargs="+", default=["claude"])
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--con-claude", action="store_true")
    parser.add_argument("--sin-juez", action="store_true")
    parser.add_argument("--solo", nargs="+", help="ids de los casos a correr")
    args = parser.parse_args()
    cases = [c for c in CASES if not args.solo or c["id"] in args.solo]
    results = []
    for layer in args.capas:
        for case in cases:
            result = run_case(case, layer, args.offline)
            print(
                f"[{layer}] {case['id']}: {result['herramientas']} palabras {result['palabras']}",
                flush=True,
            )
            results.append(result)
    if not args.sin_juez:
        judge, _used, close = make_judge(get_settings())
        try:
            for case, result in zip(cases * len(args.capas), results, strict=True):
                judge_case(case, result, judge)
                print(f"[juez] {result['caso']}: {result['puntajes']}", flush=True)
        finally:
            close()
    if args.solo and RESULTS.exists():  # se reemplazan solo esos casos en lo guardado
        fresh = {(r["caso"], r["capa"]): r for r in results}
        old = json.loads(RESULTS.read_text(encoding="utf-8"))
        results = [fresh.pop((r["caso"], r["capa"]), r) for r in old] + list(fresh.values())
    RESULTS.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
