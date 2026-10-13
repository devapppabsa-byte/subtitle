"""Capa transversal: ciclo de vida, errores, eventos y orquestación.

Este paquete no pertenece a ninguna etapa concreta del pipeline: contiene los
contratos que comparten todas ellas. Se añadió a la estructura propuesta
inicialmente porque evita duplicar el ciclo de vida y los tipos de evento en
cada subpaquete.
"""

from app.core.component import PipelineComponent
from app.core.errors import (
    AudioError,
    ConfigurationError,
    RenderError,
    SpeechError,
    SubtitleAppError,
    TranslationError,
)
from app.core.events import PipelineEvent, PipelineFinished

__all__ = [
    "PipelineComponent",
    "SubtitleAppError",
    "ConfigurationError",
    "AudioError",
    "SpeechError",
    "TranslationError",
    "RenderError",
    "PipelineEvent",
    "PipelineFinished",
]
