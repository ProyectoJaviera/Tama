"""Sprint 11: voz. Cascada ElevenLabs → Piper, tope mensual, key fuera de los logs, comandos."""

import base64
import io
import json
import logging
import wave

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from tama import voz_cli
from tama.brain import Brain
from tama.cascade import Cascade, CircuitBreaker, Layer
from tama.config import Settings
from tama.events import EventStore
from tama.voice import (
    ElevenLabsSpeaker,
    Speech,
    Usage,
    Voice,
    VoiceError,
    piper_config,
    wav_to_pcm,
)
from tama.web.app import create_app
from tests.fakes import FakeConnectivity
from tests.test_web import Provider

KEY = "sk_secreta_que_nunca_se_ve"


class FakePiper:
    name = "piper"
    available = True

    def __init__(self) -> None:
        self.said: list[str] = []

    def speak(self, text: str) -> Speech:
        self.said.append(text)
        return Speech(b"RIFF", "audio/wav", "piper")

    def warm(self) -> None:
        pass


class FakeEleven:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.said: list[str] = []

    def speak(self, text: str) -> Speech:
        if self.fail:
            raise VoiceError("ElevenLabs respondió 500.")
        self.said.append(text)
        return Speech(b"ID3", "audio/mpeg", "elevenlabs")


def make_voice(tmp_path, eleven=None, cap=10000, online=True):
    piper = FakePiper()
    voice = Voice(piper, Usage(tmp_path / "voz.db"), eleven, cap, lambda: online, CircuitBreaker(2))
    return voice, piper


def test_elevenlabs_speaks_and_counts_characters(tmp_path):
    eleven = FakeEleven()
    voice, piper = make_voice(tmp_path, eleven)
    assert voice.speak("Hola Renzo").provider == "elevenlabs"
    assert voice.usage.month() == len("Hola Renzo") and piper.said == []


def test_a_failing_elevenlabs_falls_back_to_piper_and_trips_the_breaker(tmp_path):
    voice, _ = make_voice(tmp_path, FakeEleven(fail=True))
    assert voice.speak("uno").provider == "piper"
    assert "500" in voice.last_reason
    voice.speak("dos")
    assert not voice.breaker.allow()
    assert voice.speak("tres").provider == "piper"
    assert "varias veces" in voice.last_reason
    assert voice.usage.month() == 0


def test_the_monthly_cap_sends_to_piper(tmp_path):
    eleven = FakeEleven()
    voice, _ = make_voice(tmp_path, eleven, cap=12)
    assert voice.speak("Hola Renzo").provider == "elevenlabs"
    assert voice.speak("otra frase larga").provider == "piper"
    assert "tope de 12" in voice.last_reason
    assert voice.status()["active"] == "Piper (local)" or voice.status()["used"] == 10


def test_without_internet_or_key_piper_speaks(tmp_path):
    voice, _ = make_voice(tmp_path, FakeEleven(), online=False)
    assert voice.speak("hola").provider == "piper" and voice.last_reason == "sin internet"
    voice, _ = make_voice(tmp_path, None)
    assert voice.speak("hola").provider == "piper"
    assert voice.status()["active"] == "Piper (local)"


def test_the_key_never_shows_in_errors_or_logs(caplog):
    caplog.set_level(logging.DEBUG)

    def broken(url, **kwargs):
        raise httpx.ConnectError(f"fallo con {kwargs['headers']['xi-api-key']}")

    speaker = ElevenLabsSpeaker(KEY, "voz1", post=broken)
    with pytest.raises(VoiceError) as error:
        speaker.speak("hola")
    assert KEY not in str(error.value) and KEY not in caplog.text

    def refused(url, **kwargs):
        return httpx.Response(401, json={"detail": {"message": "invalid key"}})

    with pytest.raises(VoiceError) as error:
        ElevenLabsSpeaker(KEY, "voz1", post=refused).speak("hola")
    assert "401" in str(error.value) and KEY not in str(error.value)


def test_elevenlabs_request_uses_flash_and_the_header(tmp_path):
    sent = {}

    def post(url, **kwargs):
        sent.update(url=url, **kwargs)
        return httpx.Response(200, content=b"ID3audio")

    speech = ElevenLabsSpeaker(KEY, "voz1", post=post).speak("hola")
    assert sent["url"].endswith("/v1/text-to-speech/voz1")
    assert sent["headers"]["xi-api-key"] == KEY
    assert sent["json"]["model_id"] == "eleven_flash_v2_5"
    assert speech.media_type == "audio/mpeg" and speech.audio == b"ID3audio"


def wav_bytes(seconds: float, rate: int = 48000, channels: int = 1) -> bytes:
    samples = (np.sin(np.linspace(0, 440 * seconds, int(rate * seconds))) * 8000).astype(np.int16)
    if channels == 2:
        samples = np.repeat(samples, 2)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as target:
        target.setnchannels(channels)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(samples.tobytes())
    return buffer.getvalue()


def test_audio_is_turned_into_16k_mono_for_whisper():
    pcm = wav_to_pcm(wav_bytes(2.0, rate=48000, channels=2))
    assert pcm.dtype == np.float32 and abs(len(pcm) - 32000) <= 1


def test_old_piper_configs_are_read_without_touching_the_download(tmp_path):
    model = tmp_path / "voz.onnx"
    original = tmp_path / "voz.onnx.json"
    original.write_text(json.dumps({"phoneme_type": "PhonemeType.ESPEAK"}), encoding="utf-8")
    fixed = piper_config(model, tmp_path / "datos")
    assert fixed.parent == tmp_path / "datos"
    assert json.loads(fixed.read_text(encoding="utf-8"))["phoneme_type"] == "espeak"
    assert "PhonemeType" in original.read_text(encoding="utf-8")


# --- web ----------------------------------------------------------------------------------


class FakeListener:
    available = True

    def transcribe(self, audio: bytes) -> str:
        return "hola tama" if audio else ""


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setenv("TAMA_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memoria",
        web_tailscale_host="pc.tail1.ts.net",
    )
    cascade = Cascade([Layer(Provider())], FakeConnectivity())
    brain = Brain(cascade, EventStore(tmp_path / "data" / "events.db"), None, tools=None)
    app = create_app(brain, settings)
    voice, _ = make_voice(tmp_path, FakeEleven())
    voice.listener = FakeListener()
    app.state.voice = voice
    return app, voice


def csrf(client) -> str:
    return client.get("/estado").text.split('data-csrf="')[1].split('"')[0]


def test_web_listens_and_speaks(site, monkeypatch):
    app, voice = site
    # Las rutas leen la voz armada en create_app: se reemplaza por la de prueba.
    import tama.web.app as web

    monkeypatch.setattr(web.voice_module, "build", lambda *a, **k: voice)
    app = create_app(app.state.session.brain, Settings(data_dir=voice.usage.path.parent))
    pc = TestClient(app, base_url="http://127.0.0.1")
    token = csrf(pc)
    heard = pc.post("/voz/escuchar", content=wav_bytes(1.0), headers={"x-csrf-token": token})
    assert heard.json() == {"text": "hola tama"}
    spoken = pc.post("/voz/hablar", data={"texto": "Hola Renzo"}, headers={"x-csrf-token": token})
    assert spoken.headers["x-tama-voz"] == "ElevenLabs" and spoken.content == b"ID3"
    estado = pc.get("/estado").text
    assert "Voz activa: <b>ElevenLabs</b>" in estado and "10 de 10000 caracteres" in estado
    page = pc.get("/").text
    assert 'id="hablar"' in page and "/static/voz.js" in page


def test_voice_needs_a_session_from_outside(site):
    app, _ = site
    phone = TestClient(
        app, base_url="https://pc.tail1.ts.net", headers={"Tailscale-User-Login": "r@example.com"}
    )
    assert phone.post("/voz/hablar", data={"texto": "hola"}).status_code == 401
    assert phone.post("/voz/escuchar", content=b"x").status_code == 401


# --- comandos -------------------------------------------------------------------------------


def api(handler) -> voz_cli.Api:
    client = httpx.Client(base_url="https://api.test", transport=httpx.MockTransport(handler))
    return voz_cli.Api(KEY, client)


def test_design_saves_the_chosen_preview(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, json.loads(request.content or b"{}")))
        assert request.headers["xi-api-key"] == KEY
        if request.url.path == "/v1/text-to-voice/design":
            audio = base64.b64encode(b"ID3").decode()
            previews = [{"audio_base_64": audio, "generated_voice_id": f"g{n}"} for n in (1, 2, 3)]
            return httpx.Response(200, json={"previews": previews})
        return httpx.Response(200, json={"voice_id": "voz-final"})

    played = []
    voice_id = voz_cli.design(api(handler), "serena", tmp_path, lambda _: "2", played.append)
    assert voice_id == "voz-final" and len(played) == 3
    assert calls[0][1]["voice_description"] == "serena"
    assert 100 <= len(calls[0][1]["text"]) <= 1000
    assert calls[1][1]["generated_voice_id"] == "g2"
    assert calls[1][1]["played_not_selected_voice_ids"] == ["g1", "g3"]


def test_default_description_names_no_real_person():
    assert "voz" in voz_cli.DEFAULT_DESCRIPTION.lower()
    assert not any(w in voz_cli.DEFAULT_DESCRIPTION for w in ("Raphael", "Ciel", "como la de"))


def test_clone_asks_for_consent_and_checks_the_plan(tmp_path):
    sample = tmp_path / "mi-voz.wav"
    sample.write_bytes(wav_bytes(30.0, rate=16000))
    posted = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(request.url.path)
        if request.url.path == "/v1/user/subscription":
            return httpx.Response(
                200, json={"can_use_instant_voice_cloning": False, "tier": "free"}
            )
        return httpx.Response(200, json={"voice_id": "x"})

    assert voz_cli.clone(api(handler), sample, lambda _: "no") == ""
    assert posted == []
    with pytest.raises(VoiceError, match="no permite clonar"):
        voz_cli.clone(api(handler), sample, lambda _: "sí")
    assert "/v1/voices/add" not in posted


def test_clone_uploads_a_trimmed_normalized_sample(tmp_path):
    sample = tmp_path / "mi-voz.wav"
    sample.write_bytes(wav_bytes(150.0, rate=16000, channels=2))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/user/subscription":
            return httpx.Response(200, json={"can_use_instant_voice_cloning": True})
        assert b'name="files"' in request.content
        return httpx.Response(200, json={"voice_id": "clonada"})

    assert voz_cli.clone(api(handler), sample, lambda _: "si") == "clonada"
    trimmed = voz_cli.clone_sample(sample)
    with wave.open(io.BytesIO(trimmed)) as result:
        assert result.getnchannels() == 1
        assert result.getnframes() / result.getframerate() == pytest.approx(120, abs=0.1)
    short = tmp_path / "corta.wav"
    short.write_bytes(wav_bytes(3.0))
    with pytest.raises(VoiceError, match="al menos 10"):
        voz_cli.clone_sample(short)


def test_commands_without_a_key_explain_what_to_add(capsys):
    assert voz_cli.main(Settings(elevenlabs_api_key=None), ["listar"]) == 1
    assert "ELEVENLABS_API_KEY" in capsys.readouterr().err


def test_audio_context_fits_the_length_of_the_message():
    from tama.voice import audio_context

    assert audio_context(16000 * 4) == 4 * 50 + 128
    assert audio_context(16000 * 60) == 1500  # nunca más que la ventana completa de 30 s


# --- Escuchar en cascada: Scribe → whisper local ---------------------------------------------


class LocalWhisper:
    available = True

    def __init__(self):
        self.calls = 0

    def transcribe(self, audio: bytes) -> str:
        self.calls += 1
        return "desde whisper"


def scribe_post(response=None, error=None):
    sent = []

    def post(url, **kwargs):
        sent.append({"url": url, **kwargs})
        if error is not None:
            raise error
        return response

    return post, sent


def hearing_with(tmp_path, post, cap=60, online=True):
    from tama.voice import Hearing, ScribeListener

    local = LocalWhisper()
    hearing = Hearing(
        local,
        Usage(tmp_path / "voz.db", "scribe"),
        ScribeListener(KEY, post=post),
        cap,
        lambda: online,
        CircuitBreaker(3, 300.0),
    )
    return hearing, local


def test_scribe_transcribes_first_and_counts_the_seconds(tmp_path, capsys):
    post, sent = scribe_post(httpx.Response(200, json={"text": " Esto es una prueba de sonido "}))
    hearing, local = hearing_with(tmp_path, post)
    assert hearing.transcribe(wav_bytes(3, rate=16000)) == "Esto es una prueba de sonido"
    assert local.calls == 0 and hearing.last_provider == "Scribe"
    assert sent[0]["url"].endswith("/v1/speech-to-text")
    assert sent[0]["headers"]["xi-api-key"] == KEY
    assert sent[0]["data"]["model_id"] == "scribe_v2" and sent[0]["data"]["language_code"] == "es"
    assert sent[0]["timeout"] == 5.0
    assert hearing.usage.month() == pytest.approx(3, abs=0.01)
    assert "transcribió Scribe" in capsys.readouterr().err
    assert hearing.status()["active"] == "Scribe"


def test_scribe_refused_falls_back_to_whisper_without_showing_the_key(tmp_path, capsys):
    post, _ = scribe_post(httpx.Response(401, json={"detail": "invalid api key"}))
    hearing, local = hearing_with(tmp_path, post)
    assert hearing.transcribe(wav_bytes(1, rate=16000)) == "desde whisper"
    assert local.calls == 1 and "401" in hearing.last_reason
    assert hearing.usage.month() == 0
    err = capsys.readouterr().err
    assert "whisper local" in err and KEY not in err


def test_scribe_timeout_falls_back_and_three_failures_open_the_breaker(tmp_path):
    post, sent = scribe_post(error=httpx.ReadTimeout("lento"))
    hearing, _ = hearing_with(tmp_path, post)
    assert hearing.transcribe(wav_bytes(1, rate=16000)) == "desde whisper"
    assert hearing.last_reason == "Scribe no respondió en 5 s"
    for _ in range(3):
        assert hearing.transcribe(wav_bytes(1, rate=16000)) == "desde whisper"
    assert len(sent) == 3  # al tercer fallo se deja de llamar a Scribe por unos minutos
    assert "varias veces" in hearing.status()["reason"]


def test_scribe_cap_reached_or_no_internet_uses_whisper(tmp_path, capsys):
    post, sent = scribe_post(httpx.Response(200, json={"text": "nube"}))
    hearing, _ = hearing_with(tmp_path, post, cap=0.05)  # 3 s al mes
    assert hearing.transcribe(wav_bytes(2.5, rate=16000)) == "nube"
    assert "80 %" in capsys.readouterr().err  # 2,5 de 3 s: pasó el 80 %, se avisa
    assert hearing.transcribe(wav_bytes(1, rate=16000)) == "desde whisper"
    assert "tope de 0.05 minutos" in hearing.last_reason
    offline, _ = hearing_with(tmp_path / "otro", post, online=False)
    assert offline.transcribe(wav_bytes(1, rate=16000)) == "desde whisper"
    assert offline.last_reason == "sin internet" and len(sent) == 1


def test_scribe_cap_reads_from_the_env_alias(monkeypatch):
    monkeypatch.setenv("TAMA_SCRIBE_TOPE_MIN_MES", "15")
    assert Settings(_env_file=None).scribe_monthly_minutes == 15
