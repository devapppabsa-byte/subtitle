"""Fase 3: Groq/Whisper con cliente falsificado (sin red ni clave)."""

from __future__ import annotations

import numpy as np
import pytest

from app.core.errors import ConfigurationError, SpeechError
from app.speech.groq import (
    GroqSpeechRecognizer,
    detect_language,
    encode_wav,
    normalize_transcription,
    process_transcription_result,
    resolve_model,
    transcribe_audio,
)

from conftest import make_chunk


class _FakeTranscriptions:
    def __init__(self, parent):
        self._parent = parent

    def create(self, **kwargs):
        self._parent.calls.append(kwargs)
        if self._parent.fail_with is not None:
            raise self._parent.fail_with
        return {
            "text": "  hola   mundo ",
            "language": "es",
            "words": [
                {"word": "hola", "start": 0.0, "end": 0.4},
                {"word": "mundo", "start": 0.4, "end": 0.9},
            ],
            "segments": [{"start": 0.0, "end": 0.9}],
        }


class _FakeClient:
    def __init__(self, models=("whisper-large-v3-turbo", "whisper-large-v3"), fail_with=None):
        self._models = list(models)
        self.calls: list[dict] = []
        self.fail_with = fail_with
        self.audio = type("A", (), {"transcriptions": _FakeTranscriptions(self)})()

    class _M:
        def __init__(self, id):
            self.id = id

    class _L:
        def __init__(self, data):
            self.data = data

    def models_list(self):
        raise AssertionError("usar .models.list()")

    @property
    def models(self):
        parent = self

        class _Models:
            def list(self):
                return _FakeClient._L([_FakeClient._M(i) for i in parent._models])

        return _Models()


def test_normalize_collapses_whitespace():
    assert normalize_transcription("  hola   mundo \n") == "hola mundo"
    assert normalize_transcription("") == ""


def test_process_result_extracts_text_language_words():
    resp = {
        "text": "  hola   mundo ",
        "language": "es",
        "words": [
            {"word": "hola", "start": 0.0, "end": 0.4},
            {"word": "mundo", "start": 0.4, "end": 0.9},
        ],
        "segments": [{"start": 0.0, "end": 0.9}],
    }
    payload = process_transcription_result(resp, fallback_language="en")
    assert payload.text == "hola mundo"
    assert payload.language == "es"
    assert [w.text for w in payload.words] == ["hola", "mundo"]
    assert payload.words[0].start_time == pytest.approx(0.0)


def test_detect_language_no_retranscribe():
    resp = {"text": "hello", "language": "en"}
    assert detect_language(resp) == "en"
    assert detect_language({"text": "hola"}, fallback="es") == "es"


def test_transcribe_audio_single_verbose_call():
    client = _FakeClient()
    wav = encode_wav(np.zeros(16000, dtype=np.float32), 16000)
    resp = transcribe_audio(wav, client=client, model="whisper-large-v3-turbo", language=None)
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["model"] == "whisper-large-v3-turbo"
    assert call["response_format"] == "verbose_json"
    assert "language" not in call  # auto: se omite, el idioma viene en la respuesta
    assert resp["language"] == "es"


def test_transcribe_audio_passes_fixed_language():
    client = _FakeClient()
    wav = encode_wav(np.zeros(8000, dtype=np.float32), 16000)
    transcribe_audio(wav, client=client, model="whisper-large-v3-turbo", language="en")
    assert client.calls[0]["language"] == "en"


def test_transcribe_audio_empty_raises():
    with pytest.raises(SpeechError):
        transcribe_audio(b"", client=_FakeClient())


def test_transcribe_audio_api_error_wraps():
    client = _FakeClient(fail_with=RuntimeError("429 rate limit"))
    with pytest.raises(SpeechError, match="429"):
        transcribe_audio(b"1234", client=client)


def test_resolve_model_falls_back_when_missing():
    assert resolve_model(_FakeClient(models=("whisper-large-v3",)), "whisper-large-v3-turbo") == (
        "whisper-large-v3"
    )
    assert resolve_model(_FakeClient(), "whisper-large-v3-turbo") == "whisper-large-v3-turbo"


def test_recognizer_window_emits_final_with_language():
    client = _FakeClient()
    rec = GroqSpeechRecognizer(
        api_key="fake", model="whisper-large-v3-turbo", window_seconds=2.0,
        min_seconds=0.5, client=client,
    )
    rec.configure(16_000, 1)
    rec.start()
    try:
        for _ in range(4):  # 4 × 0.5 s = 2.0 s → cierra una ventana
            rec.feed(make_chunk(0.5))
        result = rec.results.get(timeout=5.0)
    finally:
        rec.stop()
    assert result.is_final is True
    assert result.text == "hola mundo"
    assert result.language == "es"
    assert len(client.calls) == 1  # una sola llamada por ventana


def test_recognizer_flush_short_window():
    client = _FakeClient()
    rec = GroqSpeechRecognizer(
        api_key="fake", window_seconds=10.0, min_seconds=0.4, client=client,
    )
    rec.configure(16_000, 1)
    rec.start()
    try:
        rec.feed(make_chunk(0.5))
        rec.flush()
        result = rec.results.get(timeout=5.0)
    finally:
        rec.stop()
    assert result.text == "hola mundo"


def test_factory_groq_requires_key(monkeypatch):
    from app.core.factory import build_recognizer
    from app.settings.config import AppSettings

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    s = AppSettings()
    s.audio.source = "synthetic"
    s.speech.engine = "groq"
    with pytest.raises(ConfigurationError, match="GROQ_API_KEY"):
        build_recognizer(s)


def test_factory_groq_builds(monkeypatch):
    from app.core.factory import build_recognizer
    from app.settings.config import AppSettings
    from app.speech.groq import GroqSpeechRecognizer as G

    monkeypatch.setenv("GROQ_API_KEY", "fake")
    s = AppSettings()
    s.speech.engine = "groq"
    s.speech.model = "base"
    rec = build_recognizer(s)
    assert isinstance(rec, G)
    assert rec._preferred_model == "whisper-large-v3-turbo"
