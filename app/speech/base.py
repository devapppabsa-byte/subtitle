"""Interfaz ``SpeechRecognizer``.

Abstrae el motor de voz. El objetivo de la Fase 0 es poder empezar con un
simulador (``ScriptedSpeechRecognizer``), pasar a *faster-whisper* local y más
tarde a una API en la nube en streaming, sin modificar el pipeline ni la UI.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from queue import Queue

from app.audio.models import AudioChunk
from app.core.component import PipelineComponent
from app.speech.models import RecognitionResult

__all__ = ["SpeechRecognizer"]


class SpeechRecognizer(PipelineComponent, ABC):
    """Convierte audio en hipótesis de texto, parciales y finales.

    Contrato de concurrencia
    -------------------------
    * ``feed`` y ``flush`` son seguros desde el hilo de captura del pipeline.
    * La implementación puede procesar en su propio *worker thread* y publicar
      resultados de forma asíncrona en ``results``. El pipeline se limita a
      consumir esa cola.
    * ``stop`` debe **unir sus hilos** y garantizar que, al volver, todos los
      resultados pendientes ya están encolados. Así el pipeline puede drenar la
      cola sin perder la última transcripción.
    * ``results`` debe seguir siendo usable después de ``stop`` (el pipeline le
      inyecta un centinela de fin).
    """

    @abstractmethod
    def configure(self, sample_rate: int, channels: int) -> None:
        """Informa el formato del audio que llegará por ``feed``.

        Se llama una sola vez, después de arrancar la fuente y antes del primer
        ``feed``. Los motores en la nube lo necesitan para abrir el stream.
        """

    @abstractmethod
    def feed(self, chunk: AudioChunk) -> None:
        """Entrega un fragmento de audio al motor."""

    @abstractmethod
    def flush(self) -> None:
        """Fuerza la emisión de los resultados finales del audio en búfer."""

    @property
    @abstractmethod
    def results(self) -> "Queue[RecognitionResult]":
        """Cola de resultados. Los parciales y finales llegan por aquí."""
