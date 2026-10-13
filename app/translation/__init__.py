"""Traducción de texto.

Contrato: ``Translator`` expone ``translate`` como una llamada síncrona que el
pipeline ejecuta en un hilo dedicado.
"""

from app.translation.base import Translator
from app.translation.mapping import MappingTranslator
from app.translation.models import TranslationResult
from app.translation.passthrough import PassthroughTranslator

__all__ = ["Translator", "TranslationResult", "PassthroughTranslator", "MappingTranslator"]
