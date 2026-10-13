"""Validación e inmutabilidad de los modelos de dominio."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from app.audio.models import AudioChunk
from app.core.errors import AudioError
from app.subtitles.models import SubtitleCue, SubtitlePosition, SubtitleStyle


def test_audio_chunk_reports_duration_and_end_time():
    chunk = AudioChunk(samples=np.zeros(8_000, dtype=np.float32), sample_rate=16_000, start_time=1.0)
    assert chunk.frame_count == 8_000
    assert chunk.duration == pytest.approx(0.5)
    assert chunk.end_time == pytest.approx(1.5)


def test_audio_chunk_requires_mono_float32():
    with pytest.raises(AudioError):
        AudioChunk(samples=np.zeros((10, 2), dtype=np.float32), sample_rate=16_000)
    with pytest.raises(AudioError):
        AudioChunk(samples=np.zeros(10, dtype=np.int16), sample_rate=16_000)


def test_audio_chunk_rejects_bad_sample_rate():
    with pytest.raises(AudioError):
        AudioChunk(samples=np.zeros(10, dtype=np.float32), sample_rate=0)


def test_subtitle_cue_is_immutable():
    cue = SubtitleCue(cue_id="u0", text="hola", is_final=True, start_time=0.0)
    with pytest.raises(FrozenInstanceError):
        cue.text = "otra"  # type: ignore[misc]


def test_subtitle_style_defaults_are_bottom_anchored():
    style = SubtitleStyle()
    assert style.position is SubtitlePosition.BOTTOM
    assert style.max_lines >= 1
