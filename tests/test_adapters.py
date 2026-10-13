"""Adaptadores por defecto: fuente sintética, WAV, simulador de voz, traducción."""

from __future__ import annotations

import wave

import numpy as np
import pytest

from app.core.errors import AudioError
from app.audio.synthetic import SyntheticAudioSource
from app.audio.wav import WavFileAudioSource
from app.overlay.console import ConsoleRenderer
from app.speech.scripted import ScriptedSpeechRecognizer, ScriptedUtterance
from app.subtitles.models import SubtitleCue
from app.translation.mapping import MappingTranslator
from app.translation.passthrough import PassthroughTranslator

from conftest import make_chunk


# -- SyntheticAudioSource ----------------------------------------------------
def test_synthetic_source_produces_all_chunks_then_finishes():
    source = SyntheticAudioSource(sample_rate=16_000, chunk_duration=0.5, total_duration=1.5)
    source.start()
    chunks = []
    while (chunk := source.read()) is not None:
        chunks.append(chunk)

    assert len(chunks) == 3
    assert chunks[0].duration == pytest.approx(0.5)
    assert chunks[2].start_time == pytest.approx(1.0)
    assert source.running is False


# -- WavFileAudioSource ------------------------------------------------------
def _write_wav(path, *, channels: int = 2, seconds: float = 0.3, sample_rate: int = 16_000) -> None:
    frames = int(sample_rate * seconds)
    data = np.zeros((frames, channels), dtype="<i2")
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(data.tobytes())


def test_wav_source_downsamples_to_mono_float32(tmp_path):
    path = tmp_path / "audio.wav"
    _write_wav(path)
    source = WavFileAudioSource(path, chunk_duration=0.1)
    source.start()
    chunks = []
    while (chunk := source.read()) is not None:
        chunks.append(chunk)

    assert len(chunks) == 3
    assert chunks[0].samples.dtype == np.float32
    assert chunks[0].samples.ndim == 1
    assert source.running is False


def test_wav_source_raises_for_missing_file(tmp_path):
    source = WavFileAudioSource(tmp_path / "no-existe.wav")
    with pytest.raises(AudioError):
        source.start()


# -- ScriptedSpeechRecognizer ------------------------------------------------
def test_scripted_recognizer_emits_partials_then_final():
    recognizer = ScriptedSpeechRecognizer(
        [ScriptedUtterance("uno dos tres", 0.0, 2.0)], emit_partials=True
    )
    recognizer.configure(16_000, 1)
    recognizer.start()
    for _ in range(4):
        recognizer.feed(make_chunk(0.5))

    results = []
    while not recognizer.results.empty():
        results.append(recognizer.results.get())

    assert results[-1].is_final is True
    assert results[-1].text == "uno dos tres"
    partials = [r.text for r in results if not r.is_final]
    assert partials and all("uno dos tres".startswith(p) for p in partials)


def test_scripted_recognizer_flush_finalizes_pending_audio():
    recognizer = ScriptedSpeechRecognizer([ScriptedUtterance("hola mundo", 0.0, 5.0)])
    recognizer.configure(16_000, 1)
    recognizer.feed(make_chunk(0.5))
    recognizer.flush()

    final = None
    while not recognizer.results.empty():
        item = recognizer.results.get()
        if item.is_final:
            final = item
    assert final is not None and final.text == "hola mundo"


# -- Translators -------------------------------------------------------------
def test_passthrough_translator_returns_source_text():
    result = PassthroughTranslator().translate("hello", "auto", "es")
    assert result.translated_text == "hello"
    assert result.source_language == "und"
    assert result.target_language == "es"


def test_mapping_translator_uses_dictionary_case_insensitively():
    translator = MappingTranslator({"hello world": "hola mundo"}, source_language="en")
    assert translator.translate("HELLO WORLD", "auto", "es").translated_text == "hola mundo"
    assert translator.translate("desconocido", "auto", "es").translated_text == "desconocido"


# -- ConsoleRenderer ---------------------------------------------------------
def test_console_renderer_prints_partials_and_finals(capsys):
    renderer = ConsoleRenderer()
    renderer.start()
    renderer.render(SubtitleCue(cue_id="u0", text="hola", is_final=False, start_time=0.0))
    renderer.render(SubtitleCue(cue_id="u0", text="hola mundo", is_final=True, start_time=0.0))
    renderer.clear()
    renderer.stop()
    output = capsys.readouterr().out
    assert "hola" in output
    assert "hola mundo" in output
