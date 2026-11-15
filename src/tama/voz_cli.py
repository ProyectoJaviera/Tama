"""`tama voz ...`: preparar la voz de ElevenLabs desde el PC. Los ejecuta Renzo en su cuenta.

    tama voz disenar ["descripción"]   crea una voz original desde texto (Voice Design)
    tama voz listar                    voces propias y de la biblioteca en español
    tama voz clonar <archivo>          clona una voz propia o con permiso de su dueño
    tama voz probar ["texto"]          dice algo con la voz activa y guarda el audio

Ningún comando escribe el .env: muestran el voice_id para que Renzo lo pegue. La API key solo
viaja en la cabecera `xi-api-key` y nunca se imprime.
"""

import base64
import io
import os
import sys
import time
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from tama.config import Settings
from tama.net import tls_context
from tama.voice import ELEVENLABS, VoiceError

DEFAULT_DESCRIPTION = (
    "Voz femenina adulta, serena y analítica, cálida pero precisa, como una asistente "
    "inteligente que acompaña el día. Español latinoamericano neutro, ritmo pausado y timbre "
    "claro, sin acento marcado."
)
PREVIEW_TEXT = (
    "Hola, Renzo. Revisé tu día: tienes una reunión a las diez y te recuerdo tomar agua cada "
    "tanto. Si quieres, preparo un resumen de lo pendiente para la tarde y lo vemos con calma."
)
SAMPLE_TEXT = "Hola, Renzo. Soy Tama. Así sueno ahora."
CLONE_MAX_S = 120  # ElevenLabs recomienda 1 a 2 minutos de audio limpio
CLONE_MIN_S = 10
CONSENT = (
    "Clonar una voz exige que sea tuya o que su dueño te haya dado permiso explícito.\n"
    "No se pueden clonar voces de actores, personajes ni personas que no lo autorizaron.\n"
    "¿Esta voz es tuya o tienes ese permiso? (escribe sí para seguir): "
)

Ask = Callable[[str], str]


class Api:
    """Llamadas a ElevenLabs con la key en la cabecera. `client` se reemplaza en los tests."""

    def __init__(self, key: str, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(base_url=ELEVENLABS, timeout=60, verify=tls_context())
        self._headers = {"xi-api-key": key}

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._client.request(method, path, headers=self._headers, **kwargs)
        except httpx.HTTPError as exc:
            raise VoiceError(f"ElevenLabs no respondió ({type(exc).__name__}).") from None
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", "")
                detail = detail.get("message", "") if isinstance(detail, dict) else detail
            except ValueError:
                detail = ""
            raise VoiceError(f"ElevenLabs respondió {response.status_code}. {detail}".strip())
        return response.json()


def api_from(settings: Settings, client: httpx.Client | None = None) -> Api:
    key = settings.elevenlabs_api_key
    if key is None:
        raise VoiceError(
            "Falta la API key de ElevenLabs. Agrega en tu .env la línea "
            "ELEVENLABS_API_KEY=<tu key> (ver README, sección Voz)."
        )
    return Api(key.get_secret_value(), client)


def design(api: Api, description: str, folder: Path, ask: Ask, play: Callable[[Path], None]) -> str:
    """Genera voces de prueba desde la descripción, Renzo elige una y queda guardada."""
    print("Generando voces de prueba (unos segundos)…", flush=True)
    data = api.request(
        "POST",
        "/v1/text-to-voice/design",
        json={"voice_description": description, "text": PREVIEW_TEXT},
    )
    previews = data.get("previews") or []
    if not previews:
        raise VoiceError("ElevenLabs no devolvió voces de prueba. Prueba otra descripción.")
    folder.mkdir(parents=True, exist_ok=True)
    for number, preview in enumerate(previews, 1):
        path = folder / f"prueba-{number}.mp3"
        path.write_bytes(base64.b64decode(preview["audio_base_64"]))
        print(f"  {number}. {path}")
        play(path)
    choice = ask(f"¿Cuál te gusta? (1-{len(previews)}, Enter para ninguna): ").strip()
    if not choice.isdigit() or not 1 <= int(choice) <= len(previews):
        return ""
    chosen = previews[int(choice) - 1]["generated_voice_id"]
    others = [p["generated_voice_id"] for p in previews if p["generated_voice_id"] != chosen]
    saved = api.request(
        "POST",
        "/v1/text-to-voice",
        json={
            "voice_name": "Tama",
            "voice_description": description,
            "generated_voice_id": chosen,
            "played_not_selected_voice_ids": others,
        },
    )
    return str(saved.get("voice_id", ""))


def list_voices(api: Api) -> list[str]:
    lines = ["Tus voces:"]
    mine = api.request("GET", "/v2/voices", params={"page_size": 50}).get("voices", [])
    lines += [f"  {v['name']}  voice_id={v['voice_id']}" for v in mine] or ["  (ninguna)"]
    lines.append("Biblioteca en español (algunas):")
    shared = api.request(
        "GET", "/v1/shared-voices", params={"language": "es", "page_size": 20}
    ).get("voices", [])
    for voice in shared:
        traits = ", ".join(str(voice.get(k)) for k in ("gender", "accent", "age") if voice.get(k))
        lines.append(f"  {voice['name']} ({traits})  voice_id={voice['voice_id']}")
    lines.append(
        "Para usar una de la biblioteca, agrégala a tus voces en elevenlabs.io (Voice Library)."
    )
    return lines


def clone_sample(path: Path) -> bytes:
    """WAV mono de hasta 2 minutos, normalizado: lo que recomienda Instant Voice Cloning."""
    import numpy as np

    try:
        with wave.open(str(path)) as source:
            channels, rate, width = (
                source.getnchannels(),
                source.getframerate(),
                source.getsampwidth(),
            )
            frames = source.readframes(min(source.getnframes(), rate * CLONE_MAX_S))
    except (wave.Error, EOFError) as exc:
        raise VoiceError(f"No pude leer {path.name}: usa un WAV de 16 bits ({exc}).") from None
    if width != 2:
        raise VoiceError("El archivo debe ser WAV de 16 bits.")
    pcm = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1)
    seconds = len(pcm) / rate
    if seconds < CLONE_MIN_S:
        raise VoiceError(f"La muestra dura {seconds:.0f} s; hacen falta al menos {CLONE_MIN_S}.")
    peak = float(np.abs(pcm).max()) or 1.0
    pcm = np.clip(pcm * (0.89 * 32767 / peak), -32768, 32767).astype(np.int16)  # -1 dBFS
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(pcm.tobytes())
    return buffer.getvalue()


def clone(api: Api, path: Path, ask: Ask, name: str = "Tama (clonada)") -> str:
    if ask(CONSENT).strip().lower() not in ("si", "sí"):
        print("No se clonó nada.")
        return ""
    plan = api.request("GET", "/v1/user/subscription")
    if not plan.get("can_use_instant_voice_cloning"):
        raise VoiceError(
            f"Tu plan de ElevenLabs ({plan.get('tier', '?')}) no permite clonar voces. "
            "Usa `tama voz disenar` para crear una voz original."
        )
    sample = clone_sample(path)
    saved = api.request(
        "POST",
        "/v1/voices/add",
        data={"name": name, "remove_background_noise": "true"},
        files={"files": ("muestra.wav", sample, "audio/wav")},
    )
    return str(saved.get("voice_id", ""))


def open_audio(path: Path) -> None:
    """Abre el audio con el reproductor del sistema, si se puede."""
    if hasattr(os, "startfile"):
        try:
            os.startfile(path)  # type: ignore[attr-defined]
            time.sleep(0.5)
        except OSError:
            pass


def main(settings: Settings, args: list[str], ask: Ask = input) -> int:
    action, rest = (args[0], args[1:]) if args else ("", [])
    folder = Path(settings.voice_models_dir) / "pruebas"
    try:
        if action == "disenar":
            description = " ".join(rest) or DEFAULT_DESCRIPTION
            voice_id = design(api_from(settings), description, folder, ask, open_audio)
            if voice_id:
                print("\nVoz guardada en tu cuenta. Agrega esta línea a tu .env y reinicia Tama:")
                print(f"TAMA_ELEVENLABS_VOICE_ID={voice_id}")
            else:
                print("No se guardó ninguna. Puedes repetir con otra descripción.")
            return 0
        if action == "listar":
            print("\n".join(list_voices(api_from(settings))))
            return 0
        if action == "clonar" and len(rest) == 1:
            voice_id = clone(api_from(settings), Path(rest[0]), ask)
            if voice_id:
                print("\nVoz clonada. Agrega esta línea a tu .env y reinicia Tama:")
                print(f"TAMA_ELEVENLABS_VOICE_ID={voice_id}")
            return 0
        if action == "probar":
            from tama import voice as voice_module

            speaker = voice_module.build(settings)
            started = time.perf_counter()
            speech = speaker.speak(" ".join(rest) or SAMPLE_TEXT)
            elapsed = time.perf_counter() - started
            extension = "mp3" if speech.media_type == "audio/mpeg" else "wav"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"probar.{extension}"
            path.write_bytes(speech.audio)
            why = f" ({speaker.last_reason})" if speaker.last_reason else ""
            print(f"Voz: {speech.provider}{why} en {elapsed:.1f} s → {path}")
            open_audio(path)
            return 0
    except VoiceError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(__doc__.split("\n\n")[1])
    return 2
