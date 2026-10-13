"""Fase 7: retry, tracker, caché LRU, solape y knobs (sin red)."""

from __future__ import annotations

import pytest

from app.core.latency import LatencyTracker
from app.core.retry import is_retryable, with_retries
from app.speech.groq import strip_overlap_prefix
from app.translation.groq import TranslationCache


# -- retry -------------------------------------------------------------------
def test_retryable_only_for_transient():
    assert is_retryable(RuntimeError("429 rate limit")) is True
    assert is_retryable(RuntimeError("503 overloaded, try again")) is True
    assert is_retryable(TimeoutError("timed out")) is True
    assert is_retryable(RuntimeError("401 unauthorized")) is False
    assert is_retryable(ValueError("400 bad request")) is False


def test_with_retries_eventually_succeeds():
    attempts = []

    def _action():
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("503 overloaded")
        return "ok"

    assert with_retries(_action, max_retries=2, base_delay=0.0) == "ok"
    assert len(attempts) == 3


def test_with_retries_gives_up_and_does_not_retry_auth():
    calls = []

    def _auth():
        calls.append(1)
        raise RuntimeError("401 unauthorized")

    with pytest.raises(RuntimeError):
        with_retries(_auth, max_retries=2, base_delay=0.0)
    assert len(calls) == 1


# -- tracker -----------------------------------------------------------------
def test_tracker_spans_and_summary():
    tracker = LatencyTracker()
    tracker.mark("u0", "dispatch", at=0.0)
    tracker.mark("u0", "translating", at=0.4)
    tracker.mark("u0", "rendered", at=0.6)
    spans = tracker.durations("u0")
    assert spans["dispatch->translating"] == pytest.approx(0.4)
    assert spans["translating->rendered"] == pytest.approx(0.2)
    assert tracker.summary()["dispatch->translating"]["n"] == 1


def test_tracker_dump_jsonl(tmp_path):
    path = tmp_path / "lat.jsonl"
    tracker = LatencyTracker(sink=str(path))
    tracker.mark("u0", "dispatch", at=1.0)
    tracker.dump()
    assert "u0" in path.read_text()


# -- caché -------------------------------------------------------------------
def test_cache_hit_miss_and_eviction():
    from app.translation.models import TranslationResult

    cache = TranslationCache(max_entries=2)
    first = TranslationResult("hello", "hola", "en", "es")
    cache.put(first, "m")
    assert cache.get("hello", "en", "es", "m") == first
    assert cache.hits == 1
    assert cache.get("other", "en", "es", "m") is None
    cache.put(TranslationResult("bye", "adiós", "en", "es"), "m")
    cache.put(TranslationResult("x", "y", "en", "fr"), "m")
    assert len(cache) == 2
    assert cache.get("hello", "en", "es", "m") is None  # desalojada (LRU)


def test_cache_disabled_with_zero():
    cache = TranslationCache(max_entries=0)
    assert cache.get("hello", "en", "es", "m") is None


# -- solape ------------------------------------------------------------------
def test_strip_overlap_prefix():
    assert strip_overlap_prefix("b c d", "a b c") == "d"
    assert strip_overlap_prefix("Hola Mundo", "hola") == "Mundo"
    assert strip_overlap_prefix("nuevo texto", "otro previo") == "nuevo texto"
    assert strip_overlap_prefix("hola", "bueno hola") == ""
    assert strip_overlap_prefix("hola", "") == "hola"


def test_recognizer_keeps_overlap_tail():
    import numpy as np

    from app.speech.groq import GroqSpeechRecognizer
    from app.audio.models import AudioChunk

    rec = GroqSpeechRecognizer(api_key="fake", window_seconds=2.0, window_overlap_seconds=1.0)
    rec.configure(16_000, 1)
    chunk = AudioChunk(samples=np.zeros(32_000, dtype="float32"), sample_rate=16_000, start_time=0.0)
    rec.feed(chunk)
    assert rec._buffer.size == 16_000  # se retuvo 1 s de solape
    assert rec._buffer_start == pytest.approx(1.0)


def test_recognizer_rejects_bad_overlap():
    from app.core.errors import ConfigurationError
    from app.speech.groq import GroqSpeechRecognizer

    with pytest.raises(ConfigurationError):
        GroqSpeechRecognizer(api_key="x", window_seconds=2.0, window_overlap_seconds=2.0)


# -- config ------------------------------------------------------------------
def test_config_parses_latency_knobs():
    from app.settings.config import AppSettings, load_settings

    settings = AppSettings.from_dict(
        {
            "speech": {"window_seconds": 5.0, "window_overlap_seconds": 1.0, "max_retries": 1},
            "translation": {"cache_size": 10, "parallel": True},
            "latency": {"latency_log": "/tmp/x.jsonl", "poll_ms": 50},
        }
    )
    assert settings.speech.window_seconds == 5.0
    assert settings.translation.cache_size == 10
    assert settings.translation.parallel is True
    assert settings.latency.poll_ms == 50
    env = load_settings(environ={"SUBTITLE_SPEECH_WINDOW": "6", "SUBTITLE_TRANSLATION_PARALLEL": "false"})
    assert env.speech.window_seconds == 6.0
    assert env.translation.parallel is False


def test_factory_wires_latency_params(monkeypatch):
    import os

    from app.core.factory import build_pipeline, build_recognizer, build_translator
    from app.settings.config import AppSettings

    monkeypatch.setenv("GROQ_API_KEY", "fake")
    settings = AppSettings()
    settings.speech.engine = "groq"
    settings.translation.engine = "groq"
    settings.translation.target_languages = ["es"]
    settings.speech.window_seconds = 5.0
    settings.speech.window_overlap_seconds = 1.0
    settings.speech.max_retries = 1
    settings.translation.cache_size = 10
    rec = build_recognizer(settings)
    assert rec._window_seconds == 5.0 and rec._overlap_seconds == 1.0 and rec._max_retries == 1
    tr = build_translator(settings)
    assert tr.cache_stats["size"] == 0
    pipe = build_pipeline(settings)
    assert pipe.latency.summary() == {}
    # Sin clave la UI nunca toca la red: factory falla en construcción, no a mitad.
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    import app.core.errors as errors

    with pytest.raises(errors.ConfigurationError):
        build_recognizer(settings)
    assert os.environ.get("GROQ_API_KEY") is None
