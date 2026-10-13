"""Interfaz ``AudioSource``.

Define *de dónde* sale el audio. Cambiar de micrófono a audio del sistema, a un
archivo WAV o a un stream de red no debe tocar el resto de la aplicación: solo
se sustituye la implementación concreta.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.audio.models import AudioChunk
from app.core.component import PipelineComponent

__all__ = ["AudioSource"]


class AudioSource(PipelineComponent, ABC):
    """Fuente de audio mono ``float32`` consumible por *pull*.

    Contrato de concurrencia
    -------------------------
    * ``read`` es **bloqueante**: espera hasta ``timeout`` segundos a que haya
      un fragmento disponible. Devuelve ``None`` si se agota el tiempo de
      espera o si la fuente ya no producirá más audio.
    * El bucle de captura del pipeline es el único consumidor de ``read``.
    * Una fuente **finita** (archivo, fuente sintética) debe llamar a
      ``_mark_inactive`` cuando termina, para que el pipeline distinga "todavía
      no hay datos" de "ya no habrá más datos" y cierre el stream por sí solo.
    """

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        """Frecuencia de muestreo en Hz del audio que produce esta fuente."""

    @abstractmethod
    def read(self, timeout: float | None = None) -> AudioChunk | None:
        """Devuelve el siguiente fragmento, o ``None`` si no hay datos.

        :param timeout: segundos máximos de espera; ``None`` espera indefinidamente.
        """
