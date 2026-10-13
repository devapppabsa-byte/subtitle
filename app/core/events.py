"""Eventos que el pipeline publica hacia la capa de presentación.

El pipeline nunca llama directamente al renderizador: publica eventos en una
cola (``SubtitlePipeline.cues``) y es el bucle principal —el hilo de la UI—
quien los consume. Esta separación es la que permite usar Qt, que exige tocar
la interfaz gráfica solo desde el hilo principal.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.subtitles.models import SubtitleCue

__all__ = ["PipelineFinished", "PipelineEvent"]


@dataclass(frozen=True, slots=True)
class PipelineFinished:
    """Marca el final natural del pipeline.

    Se publica cuando la fuente de audio terminó y todas las etapas ya
    volcaron sus resultados. No confundir con una parada forzada con
    :meth:`SubtitlePipeline.stop`, que no publica este evento.
    """

    reason: str = "completed"


PipelineEvent = SubtitleCue | PipelineFinished
