"""Reconocimiento de voz.

Contrato: ``SpeechRecognizer`` consume audio y publica hipótesis parciales y
finales en su cola de resultados.
"""

from app.speech.base import SpeechRecognizer
from app.speech.models import RecognitionResult, Word
from app.speech.scripted import ScriptedSpeechRecognizer, ScriptedUtterance

__all__ = ["SpeechRecognizer", "RecognitionResult", "Word", "ScriptedSpeechRecognizer", "ScriptedUtterance"]
