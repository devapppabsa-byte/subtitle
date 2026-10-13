"""Configuración común de la suite de tests."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from app.audio.models import AudioChunk
from app.subtitles.base import SubtitleRenderer
from app.subtitles.models import SubtitleCue

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def make_chunk(duration: float = 0.5, sample_rate: int = 16_000, start_time: float = 0.0) -> AudioChunk:
    """Fragmento de silencio de la duración pedida."""
    frames = int(round(duration * sample_rate))
    return AudioChunk(
        samples=np.zeros(frames, dtype=np.float32),
        sample_rate=sample_rate,
        start_time=start_time,
    )


class CollectingRenderer(SubtitleRenderer):
    """Renderizador de prueba: guarda todo lo que recibe."""

    def __init__(self) -> None:
        super().__init__()
        self.cues: list[SubtitleCue] = []
        self.cleared = 0

    def render(self, cue: SubtitleCue) -> None:
        self.cues.append(cue)

    def clear(self) -> None:
        self.cleared += 1


@pytest.fixture
def chunk_factory():
    return make_chunk


@pytest.fixture
def collecting_renderer():
    return CollectingRenderer()
