"""Fuente de audio sintética, sin hardware.

Emite un número fijo de fragmentos (silencio o un tono) y termina sola. Sirve
para desarrollar y testear el pipeline sin micrófono ni tarjeta de sonido.
"""

from __future__ import annotations

import math
import time

import numpy as np

from app.audio.base import AudioSource
from app.audio.models import AudioChunk
from app.core.errors import AudioError

__all__ = ["SyntheticAudioSource"]


class SyntheticAudioSource(AudioSource):
    """Genera ``total_duration`` segundos de audio a ``sample_rate`` Hz.

    :param amplitude: ``0.0`` produce silencio (útil porque el reconocedor
        programado solo mira la duración); cualquier valor > 0 produce un tono.
    :param realtime: si es ``True``, respeta el ritmo real de reproducción;
        si es ``False`` (por defecto) produce tan rápido como se lo piden, para
        que los tests sean instantáneos.
    """

    def __init__(
        self,
        *,
        sample_rate: int = 16_000,
        chunk_duration: float = 0.5,
        total_duration: float = 5.0,
        frequency: float = 440.0,
        amplitude: float = 0.0,
        realtime: bool = False,
    ) -> None:
        super().__init__()
        if chunk_duration <= 0:
            raise AudioError("chunk_duration debe ser positivo")
        if total_duration < 0:
            raise AudioError("total_duration no puede ser negativo")
        self._sample_rate = sample_rate
        self._chunk_duration = chunk_duration
        self._total_duration = total_duration
        self._frequency = frequency
        self._amplitude = amplitude
        self._realtime = realtime
        self._frames_per_chunk = max(1, int(round(sample_rate * chunk_duration)))
        self._total_chunks = math.ceil(total_duration / chunk_duration) if total_duration else 0
        self._index = 0
        self._phase = 0.0
        self._deadline: float | None = None

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def _on_start(self) -> None:
        self._index = 0
        self._phase = 0.0
        self._deadline = time.monotonic() if self._realtime else None

    def _on_stop(self, timeout: float | None) -> None:
        self._deadline = None

    def read(self, timeout: float | None = None) -> AudioChunk | None:
        if not self.running or self._index >= self._total_chunks:
            self._mark_inactive()
            return None

        if self._deadline is not None:
            wait = self._deadline - time.monotonic()
            if wait > 0:
                time.sleep(wait if timeout is None else min(wait, timeout))
            self._deadline += self._chunk_duration

        if self._amplitude:
            t = np.arange(self._frames_per_chunk, dtype=np.float32) / self._sample_rate
            samples = (self._amplitude * np.sin(2 * np.pi * self._frequency * t + self._phase)).astype(
                np.float32
            )
            self._phase += 2 * np.pi * self._frequency * self._chunk_duration
        else:
            samples = np.zeros(self._frames_per_chunk, dtype=np.float32)

        chunk = AudioChunk(
            samples=samples,
            sample_rate=self._sample_rate,
            start_time=self._index * self._chunk_duration,
        )
        self._index += 1
        return chunk
