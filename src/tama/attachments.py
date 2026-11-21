"""Adjuntos (Sprint 12): fotos y documentos que Renzo le muestra a Tama.

- Se guardan en `data/adjuntos/AAAA-MM/` con un registro en `data/adjuntos.db`. Nunca se borran
  ni se sobrescriben: cada uno tiene su propio nombre con el id delante.
- Fotos: se giran según la cámara (EXIF) y se achican a 1568 px antes de mirarlas.
- PDF: el texto sale en el PC con pypdfium2; una página sin texto (escaneada) se mira con visión.
- Visión en cascada: Claude (mismo tope de gasto que la capa de Claude) → modelo local en CPU.
- Herramientas para el modelo (`AttachmentTools`): ver_imagen, leer_documento, resumir_documento
  y listar_adjuntos. Corren en el proceso de Tama, como terminar_conversacion: así usan la misma
  key, el mismo tope y la misma detección de red, y no se agregan si no hay adjuntos.
"""

from __future__ import annotations

import base64
import io
import logging
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from tama.mcp_hub import ToolInfo, ToolOutcome
from tama.selector import ALWAYS

log = logging.getLogger(__name__)

IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
PDF_TYPES = {"application/pdf": ".pdf"}
TEXT_TYPES = {"text/plain": ".txt", "text/markdown": ".md"}
LIMITS_MB = {"foto": 15, "pdf": 30, "texto": 2}
MAX_PAGES = 300
VISION_SIDE = 1568  # px: lo que Claude mira sin achicar
READ_CHARS = 6000  # texto que devuelve leer_documento por llamada
SUMMARY_CHUNK = 14000  # caracteres por parte al resumir en el PC
CLOUD_SUMMARY_MAX = 160000  # hasta aquí, Claude resume el documento entero de una vez
MAX_PER_LOOK = 5  # fotos por mirada: varias van juntas en una sola llamada de visión
LOOK_TOKENS = 1000  # respuesta de visión por foto (700 cortaba descripciones detalladas)
LOOK_TOKENS_EXTRA = 450  # por cada foto más en la misma mirada
# Encabezado de la nota del mensaje: el id es interno; con Renzo se usa el nombre del archivo.
NOTE_HEADER = (
    "Adjuntos (con Renzo nómbralos por su nombre de archivo, nunca por el id; para varias "
    "fotos, una sola llamada a ver_imagen con todos sus ids)"
)


class AttachmentError(Exception):
    """El adjunto no sirve o no se puede leer; el mensaje es para Renzo."""


def kind_of(content_type: str, name: str) -> str:
    ext = Path(name).suffix.lower()
    if content_type in IMAGE_TYPES or ext in {".jpg", ".jpeg", ".png", ".webp"}:
        return "foto"
    if content_type in PDF_TYPES or ext == ".pdf":
        return "pdf"
    if content_type in TEXT_TYPES or ext in {".txt", ".md"}:
        return "texto"
    raise AttachmentError("Solo puedo recibir fotos (JPG, PNG o WebP), PDF o texto (.txt, .md).")


def safe_name(name: str) -> str:
    stem = re.sub(r"[^\w.\-]+", "_", Path(name).name, flags=re.UNICODE).strip("._") or "adjunto"
    return stem[-80:]


SCHEMA = """
CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    size INTEGER NOT NULL,
    pages INTEGER,
    created TEXT NOT NULL
);
"""


class AttachmentStore:
    def __init__(self, data_dir: Path) -> None:
        self.root = Path(data_dir) / "adjuntos"
        self.db_path = Path(data_dir) / "adjuntos.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        return db

    def save(self, name: str, content_type: str, data: bytes) -> dict[str, Any]:
        kind = kind_of(content_type, name)
        if len(data) > LIMITS_MB[kind] * 1024 * 1024:
            raise AttachmentError(f"El archivo pesa más de {LIMITS_MB[kind]} MB.")
        if not data:
            raise AttachmentError("El archivo está vacío.")
        pages = None
        if kind == "foto":
            check_image(data)
        elif kind == "pdf":
            pages = pdf_page_count(data)
            if pages > MAX_PAGES:
                raise AttachmentError(f"El PDF tiene {pages} páginas; el máximo es {MAX_PAGES}.")
        else:
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise AttachmentError("El texto debe venir en UTF-8.") from exc
        now = datetime.now()
        folder = self.root / now.strftime("%Y-%m")
        folder.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            cur = db.execute(
                "INSERT INTO attachments (name, kind, path, size, pages, created)"
                " VALUES (?, ?, '', ?, ?, ?)",
                (
                    Path(name).name or "adjunto",
                    kind,
                    len(data),
                    pages,
                    now.isoformat(timespec="seconds"),
                ),
            )
            number = cur.lastrowid
            path = folder / f"{number:05d}-{safe_name(name)}"
            # "x": nunca se escribe encima de un archivo que ya exista.
            with open(path, "xb") as out:
                out.write(data)
            db.execute("UPDATE attachments SET path = ? WHERE id = ?", (str(path), number))
        return self.get(number)

    def get(self, ref: Any) -> dict[str, Any]:
        """Un adjunto por su id ("a3" o "3"), por la etiqueta de la nota ("… id a3)") o por su
        nombre de archivo (el más reciente con ese nombre): los modelos usan cualquiera."""
        text = str(ref).strip()
        number = ID_REF.match(text) or re.search(r"\bid a(\d+)\b", text)
        with self._db() as db:
            if number:
                row = db.execute(
                    "SELECT * FROM attachments WHERE id = ?", (int(number.group(1)),)
                ).fetchone()
            else:
                name = text.strip("«»\"' ")
                row = db.execute(
                    "SELECT * FROM attachments WHERE lower(name) = lower(?) ORDER BY id DESC",
                    (name,),
                ).fetchone()
        if row is None:
            raise AttachmentError(
                f"No encuentro el adjunto {text!r}: usa su id (como a3) o su nombre exacto."
            )
        return dict(row)

    def read(self, ref: Any) -> tuple[dict[str, Any], bytes]:
        item = self.get(ref)
        return item, Path(item["path"]).read_bytes()

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM attachments ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


NOTE_ITEM = re.compile(r"«(?P<name>[^»]+)» \((?P<kind>foto|pdf|texto)[^)]*?id (?P<id>a\d+)\)")


def split_note(content: str) -> tuple[str, list[dict[str, str]]]:
    """Separa el texto de Renzo y los adjuntos de la nota, para mostrarlos como miniaturas."""
    start = content.find(f"[{NOTE_HEADER}")
    if start < 0:
        return content, []
    items = [m.groupdict() for m in NOTE_ITEM.finditer(content[start:])]
    return content[:start].rstrip(), items


ID_REF = re.compile(r"^\s*a?(\d+)\s*$", re.IGNORECASE)


def refs_of(value: Any) -> list[str]:
    """Uno o varios adjuntos, por id o por nombre de archivo ("a3", "a3, a4", "a3 a4",
    ["a3", "foto del taller.jpg"]), sin repetir. Un nombre con espacios no se parte."""
    if isinstance(value, list):
        items = [str(i) for i in value]
    else:
        items = re.split(r"[,;\n]+", str(value))
        tokens = str(value).split()
        if tokens and all(ID_REF.match(tok.strip(",;")) for tok in tokens):
            items = [tok.strip(",;") for tok in tokens]  # "a3 a4": ids separados por espacios
    refs = list(dict.fromkeys(i.strip() for i in items if i.strip()))
    if not refs:
        raise AttachmentError("Falta el id del adjunto, como a3.")
    return refs


def label(item: dict[str, Any], order: str = "") -> str:
    """El nombre del archivo primero (así lo llama Renzo); el id va al final, para las
    herramientas. `order` distingue dos archivos con el mismo nombre ("2 de 6")."""
    pages = item.get("pages")
    extra = f", {pages} página{'s' if pages != 1 else ''}" if pages else ""
    place = f", {order}" if order else ""
    return f"«{item['name']}» ({item['kind']}{extra}{place}, id a{item['id']})"


# --- fotos y PDF ----------------------------------------------------------------------------


def check_image(data: bytes) -> None:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
    except Exception as exc:
        raise AttachmentError("No pude abrir la foto: ¿está dañada?") from exc


def for_vision(data: bytes, side: int = VISION_SIDE) -> bytes:
    """JPEG derecho (según la cámara) y de hasta `side` px por lado."""
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((side, side))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=85)
    return out.getvalue()


def pdf_page_count(data: bytes) -> int:
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(data)
    except Exception as exc:
        raise AttachmentError("No pude abrir el PDF: ¿está dañado o protegido?") from exc
    try:
        return len(doc)
    finally:
        doc.close()


def pdf_pages(data: bytes, first: int, last: int) -> list[tuple[int, str]]:
    """Texto de las páginas `first`..`last` (desde 1), cada una con su número."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(data)
    try:
        last = min(last, len(doc))
        pages = []
        for number in range(max(1, first), last + 1):
            page = doc[number - 1]
            text = page.get_textpage().get_text_bounded()
            pages.append((number, " ".join(text.split())))
        return pages
    finally:
        doc.close()


def pdf_page_image(data: bytes, number: int) -> bytes:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(data)
    try:
        image = doc[number - 1].render(scale=1.6).to_pil()
        out = io.BytesIO()
        image.convert("RGB").save(out, "JPEG", quality=85)
        return for_vision(out.getvalue())
    finally:
        doc.close()


# --- visión y resúmenes en cascada ------------------------------------------------------------


@dataclass
class Seen:
    text: str
    provider: str


class Senses:
    """Claude (si hay key, red, tope y está permitido) → modelo local en CPU."""

    def __init__(
        self,
        settings: Any,
        spend: Any = None,
        online: Callable[[], bool] = lambda: True,
        claude: Any = None,
        local: Any = None,
    ) -> None:
        self.settings = settings
        self.spend = spend
        self.online = online
        self._claude = claude
        self._local = local

    def claude_reason(self, image: bool) -> str:
        s = self.settings
        if image and not s.vision_cloud:
            return "las fotos se miran solo en el PC (TAMA_VISION_CLOUD=false)"
        if not s.claude_enabled or s.anthropic_api_key is None:
            return "sin Claude"
        if not self.online():
            return "sin internet"
        if self.spend is not None and self.spend.gate():
            return self.spend.gate()
        return ""

    def claude(self) -> Any:
        if self._claude is None:
            import anthropic
            from anthropic import DefaultHttpxClient

            from tama.net import tls_context

            self._claude = anthropic.Anthropic(
                api_key=self.settings.anthropic_api_key.get_secret_value(),
                max_retries=1,
                timeout=60,
                http_client=DefaultHttpxClient(verify=tls_context()),
            )
        return self._claude

    def local(self) -> Any:
        if self._local is None:
            from tama.ollama import OllamaClient

            self._local = OllamaClient(
                self.settings.ollama_host, timeout=self.settings.vision_timeout_s
            )
        return self._local

    def _ask_claude(self, content: list[dict[str, Any]], max_tokens: int) -> str:
        response = self.claude().messages.create(
            model=self.settings.claude_model,
            max_tokens=max_tokens,
            system=SENSES_SYSTEM,
            messages=[{"role": "user", "content": content}],
        )
        if self.spend is not None:
            self.spend.add(response.usage)
        return "".join(b.text for b in response.content if b.type == "text").strip()

    def _ask_local(self, model: str, prompt: str, images: list[bytes] | None = None) -> str:
        message: dict[str, Any] = {"role": "user", "content": prompt}
        if images:
            message["images"] = [base64.b64encode(i).decode() for i in images]
        options = {"num_ctx": 8192, "num_gpu": self.settings.num_gpu}
        if self.settings.num_thread:
            options["num_thread"] = self.settings.num_thread
        reply = self.local().chat(
            model,
            [{"role": "system", "content": SENSES_SYSTEM}, message],
            think=False,
            options=options,
        )
        return reply["message"]["content"].strip()

    def look(
        self, jpeg: bytes | list[bytes], question: str, names: list[str] | None = None
    ) -> Seen:
        """Mira una foto o varias juntas (una sola llamada: compara y ahorra)."""
        images = jpeg if isinstance(jpeg, list) else [jpeg]
        several = len(images) > 1
        default = (
            "Describe cada foto en orden, nombrándola, en 2 a 4 frases, y lo que tienen en común "
            "o de distinto."
            if several
            else "Describe la foto: qué se ve, el lugar y los detalles útiles."
        )
        prompt = question.strip() or default
        if several and names:
            prompt = "Fotos, en orden: " + ", ".join(names) + ".\n" + prompt
        reason = self.claude_reason(image=True)
        if not reason:
            try:
                content: list[dict[str, Any]] = [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": base64.b64encode(image).decode(),
                        },
                    }
                    for image in images
                ]
                content.append({"type": "text", "text": prompt})
                text = self._ask_claude(
                    content, LOOK_TOKENS + LOOK_TOKENS_EXTRA * (len(images) - 1)
                )
                return Seen(text, "Claude")
            except Exception as exc:  # cualquier falla de la nube: se mira en el PC
                reason = f"Claude no respondió ({type(exc).__name__})"
        log.info("visión en el PC: %s", reason)
        try:
            text = self._ask_local(self.settings.vision_local_model, prompt, images)
        except Exception as exc:
            raise AttachmentError(
                f"No pude mirar la foto: {reason}, y el modelo local de visión "
                f"({self.settings.vision_local_model}) no respondió ({type(exc).__name__})."
            ) from exc
        return Seen(text, f"{self.settings.vision_local_model} (en el PC; {reason})")

    def summarize(self, text: str, focus: str, title: str) -> Seen:
        ask = (
            f"Resume en español el documento «{title}» en 5 a 10 puntos breves, con lo importante."
        )
        if focus.strip():
            ask += f" Enfócate en: {focus.strip()}."
        reason = self.claude_reason(image=False)
        if not reason and len(text) <= CLOUD_SUMMARY_MAX:
            try:
                return Seen(
                    self._ask_claude(
                        [{"type": "text", "text": f"{ask}\n\n<documento>\n{text}\n</documento>"}],
                        900,
                    ),
                    "Claude",
                )
            except Exception as exc:
                reason = f"Claude no respondió ({type(exc).__name__})"
        reason = reason or "documento muy largo para una sola vez"
        # En el PC, por partes: cada parte se resume y luego se unen los resúmenes.
        model = self.settings.background_model
        parts = [text[i : i + SUMMARY_CHUNK] for i in range(0, len(text), SUMMARY_CHUNK)] or [""]
        notes = []
        for n, part in enumerate(parts, 1):
            notes.append(
                self._ask_local(
                    model,
                    f"Parte {n} de {len(parts)} de «{title}». "
                    f"Resume en 5 líneas lo importante:\n\n{part}",
                )
            )
        if len(notes) == 1:
            return Seen(notes[0], f"{model} (en el PC; {reason})")
        joined = "\n\n".join(notes)
        return Seen(
            self._ask_local(model, f"{ask}\n\nResúmenes de cada parte:\n{joined}"),
            f"{model} (en el PC; {reason})",
        )


SENSES_SYSTEM = (
    "Eres los ojos de Tama, la asistente de Renzo. Responde en español, claro y concreto. "
    "Describe solo lo que se ve o lo que dice el documento; si algo no se distingue, dilo."
)


# --- herramientas para el modelo ------------------------------------------------------------


def _schema(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


TOOLS = [
    ToolInfo(
        "adjuntos",
        "ver_imagen",
        "Mira fotos que Renzo adjuntó y responde la pregunta. Si son varias, pásalas TODAS en "
        'UNA sola llamada (adjunto: "a3, a4, a5", hasta 5): no llames una vez por foto. '
        "Úsala siempre que Renzo hable de una foto adjunta: no la supongas. Con Renzo, nombra "
        "cada foto por su nombre de archivo, no por el id.",
        _schema({"adjunto": {"type": "string"}, "pregunta": {"type": "string"}}, ["adjunto"]),
        tier=ALWAYS,
    ),
    ToolInfo(
        "adjuntos",
        "leer_documento",
        "Lee el texto de un PDF o texto adjunto (id como a4), por páginas. Para resumirlo entero, "
        "usa resumir_documento.",
        _schema(
            {
                "adjunto": {"type": "string"},
                "desde": {"type": "integer"},
                "hasta": {"type": "integer"},
            },
            ["adjunto"],
        ),
        tier=ALWAYS,
    ),
    ToolInfo(
        "adjuntos",
        "resumir_documento",
        'Resume uno o varios PDF o textos adjuntos enteros (ids como a4 o "a4, a5", todos en '
        "una llamada): un resumen por documento; enfoque opcional (p. ej., plazos). Con Renzo, "
        "nombra cada documento por su nombre de archivo, no por el id.",
        _schema({"adjunto": {"type": "string"}, "enfoque": {"type": "string"}}, ["adjunto"]),
        tier=ALWAYS,
    ),
    ToolInfo(
        "adjuntos",
        "listar_adjuntos",
        "Lista las últimas fotos y documentos que Renzo adjuntó, con su id.",
        _schema({}, []),
        tier=ALWAYS,
    ),
]
BY_NAME = {t.name: t for t in TOOLS}


class Attachments:
    """Lo que la web y el chat usan: guardar, la nota del mensaje y las herramientas."""

    def __init__(self, store: AttachmentStore, senses: Senses) -> None:
        self.store = store
        self.senses = senses

    def note(self, refs: list[str]) -> str:
        """La nota del mensaje para el modelo: los archivos por su nombre, con su id al final."""
        items = [self.store.get(r) for r in refs]
        if not items:
            return ""
        names = [i["name"] for i in items]
        total = len(items)
        listed = "; ".join(
            label(item, f"{n} de {total}" if names.count(item["name"]) > 1 else "")
            for n, item in enumerate(items, 1)
        )
        return f"[{NOTE_HEADER}: {listed}]"

    def wrap(self, tools: Any) -> AttachmentTools:
        return AttachmentTools(tools, self)

    # Lo que hace cada herramienta.
    def ver_imagen(self, adjunto: Any, pregunta: str = "") -> str:
        refs = refs_of(adjunto)
        if len(refs) > MAX_PER_LOOK:
            raise AttachmentError(
                f"Son {len(refs)} fotos: mira hasta {MAX_PER_LOOK} por vez (en varias llamadas)."
            )
        items, images = [], []
        for ref in refs:
            item, data = self.store.read(ref)
            if item["kind"] != "foto":
                raise AttachmentError(f"{label(item)} no es una foto: usa leer_documento.")
            items.append(item)
            images.append(for_vision(data))
        names = [label(i) for i in items]
        seen = self.senses.look(images if len(images) > 1 else images[0], pregunta, names)
        return f"{', '.join(names)}, mirada(s) con {seen.provider}:\n{seen.text}"

    def _document_text(
        self, item: dict[str, Any], data: bytes, first: int, last: int
    ) -> list[tuple[int, str]]:
        if item["kind"] == "texto":
            return [(1, data.decode("utf-8"))]
        pages = pdf_pages(data, first, last)
        result = []
        for number, text in pages:
            if len(text) < 20:  # página escaneada: se mira con visión, solo si hace falta
                try:
                    seen = self.senses.look(
                        pdf_page_image(data, number),
                        "Transcribe el texto de esta página, tal cual.",
                    )
                    text = f"(página escaneada, leída con {seen.provider}) {seen.text}"
                except AttachmentError as exc:
                    text = f"(página sin texto; no se pudo leer: {exc})"
            result.append((number, text))
        return result

    def leer_documento(self, adjunto: str, desde: int = 1, hasta: int | None = None) -> str:
        item, data = self.store.read(adjunto)
        if item["kind"] == "foto":
            raise AttachmentError(f"{label(item)} es una foto: usa ver_imagen.")
        total = item.get("pages") or 1
        first = max(1, int(desde or 1))
        last = min(total, int(hasta or total))
        out, used, shown = [], 0, first - 1
        for number, text in self._document_text(item, data, first, last):
            chunk = f"[pág. {number}] {text}" if item["kind"] == "pdf" else text
            if used + len(chunk) > READ_CHARS and out:
                break
            out.append(chunk[: READ_CHARS - used])
            used += len(out[-1])
            shown = number
        more = f" Sigue en la página {shown + 1} (de {total})." if shown < last else ""
        return f"{label(item)}:\n" + "\n".join(out) + more

    def resumir_documento(self, adjunto: Any, enfoque: str = "") -> str:
        """Un resumen por documento; el modelo los combina (cuida el contexto y el gasto)."""
        parts = []
        for ref in refs_of(adjunto):
            item, data = self.store.read(ref)
            if item["kind"] == "foto":
                raise AttachmentError(f"{label(item)} es una foto: usa ver_imagen.")
            pages = self._document_text(item, data, 1, item.get("pages") or 1)
            text = "\n".join(f"[pág. {n}] {t}" if item["kind"] == "pdf" else t for n, t in pages)
            seen = self.senses.summarize(text, enfoque, item["name"])
            parts.append(f"Resumen de {label(item)} ({seen.provider}):\n{seen.text}")
        return "\n\n".join(parts)

    def listar_adjuntos(self) -> str:
        items = self.store.recent()
        if not items:
            return "Renzo no ha adjuntado nada todavía."
        return "Adjuntos recientes: " + "; ".join(
            f"{label(i)} del {i['created'][:10]}" for i in items
        )


class AttachmentTools:
    """Envuelve las herramientas de Tama y agrega las de adjuntos (solo cuando hay adjuntos)."""

    def __init__(self, tools: Any, attachments: Attachments) -> None:
        self._tools = tools
        self._attachments = attachments

    def available_tools(self, max_tools: int = 10) -> tuple[list[Any], list[str]]:
        if self._tools is None:
            return list(TOOLS), []
        available, down = self._tools.available_tools(max_tools)
        return [*available, *TOOLS], down

    def find(self, tool_name: str) -> Any | None:
        if tool_name in BY_NAME:
            return BY_NAME[tool_name]
        return self._tools.find(tool_name) if self._tools is not None else None

    def call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if tool_name not in BY_NAME:
            return self._tools.call(tool_name, arguments)
        try:
            return ToolOutcome(True, getattr(self._attachments, tool_name)(**(arguments or {})))
        except AttachmentError as exc:
            return ToolOutcome(False, str(exc))
        except TypeError as exc:
            return ToolOutcome(False, f"Argumentos inválidos para {tool_name}: {exc}")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._tools, name)
