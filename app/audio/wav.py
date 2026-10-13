"""Fuente de audio basada en archivo WAV.

Implementación real y testeable que demuestra la intercambiabilidad del
``AudioSource``: el pipeline no cambia al pasar de micrófono a archivo.

Soporta WAV PCM de 16 bits, mono o multicanal (se hace *downmix* a mono). No
remuestrea: se asume que el archivo ya está a la frecuencia de trabajo.
"""

from __future__ import annotations

import time
import wave
from pathlib import Path

import numpy as np

from app.audio.base import AudioSource
from app.audio.models import AudioChunk
from app.core.errors import AudioError

__all__ = ["WavFileAudioSource"]

_INT16_MAX = 32_768.0


class WavFileAudioSource(AudioSource):
    """Lee fragmentos de un archivo WAV PCM de 16 bits."""

    def __init__(self, path: str | Path, *, chunk_duration: float = 0.5, realtime: bool = False) -> None:
        super().__init__()
        if chunk_duration <= 0:
            raise AudioError("chunk_duration debe ser positivo")
        self._path = Path(path)
        self._chunk_duration = chunk_duration
        self._realtime = realtime
        self._wave: wave.Wave_read | None = None
        self._channels = 1
        self._sample_rate = 16_000
        self._frames_per_chunk = 0
        self._frames_read = 0
        self._deadline: float | None = None

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def channels(self) -> int:
        return self._channels

    def _on_start(self) -> None:
        if not self._path.is_file():
            raise AudioError(f"No existe el archivo de audio: {self._path}")
        reader = wave.open(str(self._path), "rb")
        if reader.getsampwidth() != 2:
            reader.close()
            raise AudioError(
                f"Solo se soporta PCM de 16 bits; {self._path.name} usa {reader.getsampwidth() * 8} bits"
            )
        if reader.getcomptype() != "NONE":
            reader.close()
            raise AudioError(f"El WAV comprimido no está soportado: {self._path.name}")
        self._wave = reader
        self._channels = reader.getnchannels()
        self._sample_rate = reader.getframerate()
        self._frames_per_chunk = max(1, int(round(self._sample_rate * self._chunk_duration)))
        self._frames_read = 0
        self._deadline = time.monotonic() if self._realtime else None

    def _on_stop(self, timeout: float | None) -> None:
        if self._wave is not None:
            self._wave.close()
            self._wave = None
        self._deadline = None

    def read(self, timeout: float | None = None) -> AudioChunk | None:
        reader = self._wave
        if reader is None or not self.running:
            return None

        raw = reader.readframes(self._frames_per_chunk)
        if not raw:
            self._mark_inactive()
            return None

        if self._deadline is not None:
            wait = self._deadline - time.monotonic()
            if wait > 0:
                time.sleep(wait if timeout is None else min(wait, timeout))
            self._deadline += self._chunk_duration

        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32)
        frame_count = samples.shape[0] // self._channels
        if self._channels > 1:
            samples = samples[: frame_count * self._channels].reshape(frame_count, self._channels).mean(axis=1)
        samples = np.ascontiguousarray(samples / _INT16_MAX, dtype=np.float32)

        chunk = AudioChunk(
            samples=samples,
            sample_rate=self._sample_rate,
            start_time=self._frames_read / self._sample_rate,
        )
        self._frames_read += frame_count
        return chunk
