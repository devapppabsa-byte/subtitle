"""Traducción de texto.

Contrato: ``Translator`` expone ``translate`` como una llamada síncrona que el
pipeline ejecuta en un hilo dedicado.
"""

from app.translation.base import Translator
from app.translation.chat import ChatTranslator, CompatClient
from app.translation.groq import (
    ContextBuffer,
    GroqTranslator,
    TranslationCache,
    build_translation_prompt,
    normalize_language,
    preserve_context,
    translate_text,
    translate_to_languages,
)
from app.translation.mapping import MappingTranslator
from app.translation.models import TranslationResult
from app.translation.passthrough import PassthroughTranslator
from app.translation.providers import PROVIDERS, Provider, provider_for_engine

__all__ = [
    "Translator",
    "TranslationResult",
    "ContextBuffer",
    "GroqTranslator",
    "ChatTranslator",
    "CompatClient",
    "TranslationCache",
    "PassthroughTranslator",
    "MappingTranslator",
    "PROVIDERS",
    "Provider",
    "provider_for_engine",
    "build_translation_prompt",
    "normalize_language",
    "preserve_context",
    "translate_text",
    "translate_to_languages",
]
