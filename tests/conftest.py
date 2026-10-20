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


class FakeLlavero:
    """Llavero en memoria para tests (evita secret-tool)."""

    def __init__(self) -> None:
        self._store = {}

    def available(self) -> bool:
        return True

    def load(self):
        return self._store.get("master")

    def store(self, master: bytes) -> None:
        self._store["master"] = master


@pytest.fixture
def fake_vault(tmp_path, monkeypatch):
    """Un KeyVault con clave maestra en memoria, aislado en tmp_path."""
    import secrets

    from app.settings import keyvault

    vault = keyvault.KeyVault(
        tmp_path / "keys.db", llavero=FakeLlavero(), master=secrets.token_bytes(32)
    )
    monkeypatch.setattr(keyvault, "db_path", lambda: tmp_path / "keys.db")
    monkeypatch.setattr(keyvault, "default_vault", lambda: vault)
    return vault
