"""Voz de Tama (Sprint 11): escuchar con whisper.cpp y hablar en cascada ElevenLabs → Piper.

- Escuchar: whisper.cpp en CPU (`pywhispercpp`), con un modelo descargado al instalar
  (`scripts/descargar_voz.py`). El navegador manda el audio ya en 16 kHz mono (WAV).
- Hablar: ElevenLabs si hay key, voz, red, cortacircuitos cerrado y caracteres del mes bajo el
  tope; si no, o si falla, Piper local en español. Sin internet, Tama sigue hablando.

Nada se descarga en ejecución: si falta un modelo, la voz local queda apagada y se dice por qué.
La API key de ElevenLabs solo viaja en la cabecera `xi-api-key`; nunca se registra ni se muestra.
"""

import io
import json
import logging
import os
import sqlite3
import threading
import wave
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from tama.cascade import CircuitBreaker
from tama.net import tls_context

log = logging.getLogger(__name__)

ELEVENLABS = "https://api.elevenlabs.io"
WHISPER_PROMPT = "Conversación con Tama, la asistente de Renzo."
MAX_TEXT = 1200  # caracteres que se leen en voz alta por respuesta


class VoiceError(Exception):
    """La voz no está disponible o falló; el mensaje es para Renzo y nunca lleva la key."""


def month_key(moment: datetime | None = None) -> str:
    return (moment or datetime.now(UTC)).strftime("%Y-%m")


class Usage:
    """Caracteres enviados a ElevenLabs por mes, contados en el PC (`data/voz.db`)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS usage (month TEXT PRIMARY KEY, chars INTEGER)")

    def add(self, chars: int, month: str | None = None) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO usage (month, chars) VALUES (?, ?) ON CONFLICT(month)"
                " DO UPDATE SET chars = chars + excluded.chars",
                (month or month_key(), chars),
            )

    def month(self, month: str | None = None) -> int:
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT chars FROM usage WHERE month = ?", (month or month_key(),)
            ).fetchone()
        return row[0] if row else 0


# --- escuchar ----------------------------------------------------------------------------


def wav_to_pcm(data: bytes) -> Any:
    """WAV mono de 16 bits a float32 en 16 kHz, como lo pide whisper.cpp."""
    import numpy as np

    with wave.open(io.BytesIO(data)) as source:
        channels, rate, width = source.getnchannels(), source.getframerate(), source.getsampwidth()
        frames = source.readframes(source.getnframes())
    if width != 2:
        raise VoiceError("El audio debe venir en WAV de 16 bits.")
    pcm = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1)
    if rate != 16000 and len(pcm):
        size = int(len(pcm) * 16000 / rate)
        pcm = np.interp(np.linspace(0, len(pcm) - 1, size), np.arange(len(pcm)), pcm)
    return pcm.astype(np.float32)


class Listener:
    """whisper.cpp en CPU. El modelo se carga la primera vez y queda en memoria."""

    def __init__(self, model_path: Path, threads: int = 0, language: str = "es") -> None:
        self.model_path = model_path
        self.threads = threads
        self.language = language
        self._model = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self.model_path.exists()

    def _load(self):
        if self._model is None:
            if not self.available:
                raise VoiceError(
                    "Falta el modelo para escuchar. Instálalo con "
                    "`uv run python scripts/descargar_voz.py`."
                )
            from pywhispercpp.model import Model

            # Solo CPU (regla del PC: la GPU no se usa) y sin el detalle de carga en la consola.
            options: dict[str, Any] = {
                "print_progress": False,
                "print_realtime": False,
                "context_params": {"use_gpu": False},
                "redirect_whispercpp_logs_to": os.devnull,
            }
            if self.threads:
                options["n_threads"] = self.threads
            self._model = Model(str(self.model_path), **options)
        return self._model

    def transcribe(self, wav: bytes) -> str:
        pcm = wav_to_pcm(wav)
        if len(pcm) < 16000 * 0.3:
            return ""
        with self._lock:  # whisper.cpp no admite dos transcripciones a la vez
            segments = self._load().transcribe(
                pcm, language=self.language, initial_prompt=WHISPER_PROMPT
            )
        return " ".join(s.text.strip() for s in segments).strip()


# --- hablar ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Speech:
    audio: bytes
    media_type: str
    provider: str


def piper_config(model: Path, cache_dir: Path | None = None) -> Path:
    """Las voces v1.0.0 traen "PhonemeType.ESPEAK"; Piper 1.8 espera "espeak". Se deja una copia
    corregida en `cache_dir` (la carpeta de datos): el archivo descargado no se toca, así su hash
    sigue valiendo y la carpeta de modelos puede ir de solo lectura (Docker)."""
    original = Path(f"{model}.json")
    data = json.loads(original.read_text(encoding="utf-8"))
    kind = str(data.get("phoneme_type", ""))
    if not kind.startswith("PhonemeType."):
        return original
    fixed = (cache_dir or model.parent) / f"{model.stem}.tama.json"
    fixed.parent.mkdir(parents=True, exist_ok=True)
    data["phoneme_type"] = kind.split(".", 1)[1].lower()
    if not fixed.exists() or json.loads(fixed.read_text(encoding="utf-8")) != data:
        fixed.write_text(json.dumps(data), encoding="utf-8")
    return fixed


class PiperSpeaker:
    name = "piper"

    def __init__(self, model_path: Path, cache_dir: Path | None = None) -> None:
        self.model_path = model_path
        self.cache_dir = cache_dir
        self._voice = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self.model_path.exists() and Path(f"{self.model_path}.json").exists()

    def _load(self) -> None:
        if self._voice is None:
            from piper import PiperVoice

            self._voice = PiperVoice.load(
                str(self.model_path), config_path=str(piper_config(self.model_path, self.cache_dir))
            )

    def warm(self) -> None:
        if self.available:
            with self._lock:
                self._load()

    def speak(self, text: str) -> Speech:
        if not self.available:
            raise VoiceError(
                "Falta la voz local. Instálala con `uv run python scripts/descargar_voz.py`."
            )
        with self._lock:
            self._load()
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as target:
                self._voice.synthesize_wav(text, target)
        return Speech(buffer.getvalue(), "audio/wav", self.name)


class ElevenLabsSpeaker:
    name = "elevenlabs"

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        model: str = "eleven_flash_v2_5",
        timeout_s: float = 8.0,
        post: Callable[..., Any] | None = None,
    ) -> None:
        self._key = api_key
        self.voice_id = voice_id
        self.model = model
        self.timeout_s = timeout_s
        self._post = post or self._http_post

    def _http_post(self, url: str, **kwargs: Any) -> httpx.Response:
        return httpx.post(url, verify=tls_context(), **kwargs)

    def speak(self, text: str) -> Speech:
        try:
            response = self._post(
                f"{ELEVENLABS}/v1/text-to-speech/{self.voice_id}",
                params={"output_format": "mp3_44100_64"},
                headers={"xi-api-key": self._key, "Accept": "audio/mpeg"},
                json={"text": text, "model_id": self.model, "language_code": "es"},
                timeout=self.timeout_s,
            )
        except httpx.HTTPError as exc:
            raise VoiceError(f"ElevenLabs no respondió ({type(exc).__name__}).") from None
        if response.status_code >= 400:
            raise VoiceError(f"ElevenLabs respondió {response.status_code}.")
        return Speech(response.content, "audio/mpeg", self.name)


class Voice:
    """Hablar en cascada: ElevenLabs → Piper. Cuenta los caracteres del mes y respeta el tope."""

    def __init__(
        self,
        piper: PiperSpeaker,
        usage: Usage,
        eleven: ElevenLabsSpeaker | None = None,
        monthly_chars: int = 10000,
        online: Callable[[], bool] = lambda: True,
        breaker: CircuitBreaker | None = None,
        listener: Listener | None = None,
    ) -> None:
        self.piper = piper
        self.eleven = eleven
        self.usage = usage
        self.monthly_chars = monthly_chars
        self.online = online
        self.breaker = breaker or CircuitBreaker(3, 300.0)
        self.listener = listener
        self.last_reason = ""

    def eleven_skip_reason(self, chars: int) -> str:
        """Por qué no se usa ElevenLabs para este texto ("" si se puede)."""
        if self.eleven is None:
            return "sin API key o sin voice_id de ElevenLabs"
        if not self.online():
            return "sin internet"
        if not self.breaker.allow():
            return "ElevenLabs falló varias veces seguidas; se reintenta en unos minutos"
        if self.usage.month() + chars > self.monthly_chars:
            return f"se alcanzó el tope de {self.monthly_chars} caracteres del mes"
        return ""

    def speak(self, text: str) -> Speech:
        text = " ".join(text.split())[:MAX_TEXT]
        if not text:
            raise VoiceError("No hay nada que decir.")
        reason = self.eleven_skip_reason(len(text))
        if not reason and self.eleven is not None:
            try:
                speech = self.eleven.speak(text)
            except VoiceError as exc:
                self.breaker.failure()
                reason = str(exc)
                log.info("voz: %s; sigo con Piper", reason)
            else:
                self.breaker.success()
                self.usage.add(len(text))
                self.last_reason = ""
                return speech
        self.last_reason = reason
        return self.piper.speak(text)

    def warm(self) -> None:
        """Carga Piper y whisper en memoria (en segundo plano al abrir Tama): la primera respuesta
        hablada no espera los ~5 s de carga de Piper."""
        try:
            self.piper.warm()
            if self.listener is not None and self.listener.available:
                with self.listener._lock:
                    self.listener._load()
        except Exception as exc:  # una voz que no carga no debe tumbar Tama
            log.warning("voz: no pude precargar (%s)", type(exc).__name__)

    def status(self) -> dict[str, Any]:
        reason = self.eleven_skip_reason(1)
        return {
            "active": "ElevenLabs" if not reason else "Piper (local)",
            "reason": reason,
            "used": self.usage.month(),
            "cap": self.monthly_chars,
            "piper": self.piper.available,
            "listen": self.listener.available if self.listener else False,
        }


def build(settings: Any, online: Callable[[], bool] = lambda: True) -> Voice:
    """Arma la voz con la configuración de Tama (ver config.py)."""
    folder = Path(settings.voice_models_dir)
    key = settings.elevenlabs_api_key
    eleven = None
    if key is not None and settings.elevenlabs_voice_id.strip():
        eleven = ElevenLabsSpeaker(
            key.get_secret_value(),
            settings.elevenlabs_voice_id.strip(),
            settings.elevenlabs_model,
            settings.elevenlabs_timeout_s,
        )
    return Voice(
        PiperSpeaker(folder / settings.piper_voice, Path(settings.data_dir)),
        Usage(Path(settings.data_dir) / "voz.db"),
        eleven,
        settings.elevenlabs_monthly_chars,
        online,
        CircuitBreaker(settings.breaker_failures, settings.breaker_cooldown_s),
        Listener(folder / settings.whisper_model, settings.num_thread),
    )
