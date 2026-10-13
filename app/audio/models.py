"""Modelos de dominio de audio.

El contrato de datos entre etapas se fija aquí, no en las implementaciones.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from app.core.errors import AudioError

__all__ = ["AudioChunk", "PCMFloat"]


#: Muestras PCM en coma flotante, mono, rango ``[-1.0, 1.0]``.
PCMFloat = NDArray[np.float32]


@dataclass(frozen=True, slots=True)
class AudioChunk:
    """Fragmento de audio mono listo para el reconocedor.

    Convención única e innegociable del pipeline: **todas** las
    :class:`~app.audio.base.AudioSource` entregan audio mono ``float32``. El
    *downmix* de estéreo a mono y la conversión de formato son
    responsabilidad de la fuente, de modo que los motores de voz nunca tengan
    que conocer el dispositivo de origen.
    """

    samples: PCMFloat
    sample_rate: int
    start_time: float = 0.0
    """Instante de inicio respecto al comienzo del stream, en segundos."""

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise AudioError(f"sample_rate debe ser positivo, se recibió {self.sample_rate}")
        if self.samples.ndim != 1:
            raise AudioError(
                f"AudioChunk espera muestras mono (1-D), se recibió forma {self.samples.shape}"
            )
        if self.samples.dtype != np.float32:
            raise AudioError(
                f"AudioChunk espera muestras float32, se recibió {self.samples.dtype}"
            )

    @property
    def frame_count(self) -> int:
        """Número de muestras (fotogramas) del fragmento."""
        return int(self.samples.shape[0])

    @property
    def duration(self) -> float:
        """Duración del fragmento en segundos."""
        return self.frame_count / self.sample_rate

    @property
    def end_time(self) -> float:
        """Instante de fin respecto al comienzo del stream, en segundos."""
        return self.start_time + self.duration
