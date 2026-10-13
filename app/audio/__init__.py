"""Captura de audio.

Contrato: ``AudioSource`` entrega :class:`AudioChunk` mono ``float32``.
"""

from app.audio.base import AudioSource
from app.audio.models import AudioChunk
from app.audio.synthetic import SyntheticAudioSource
from app.audio.wav import WavFileAudioSource

__all__ = ["AudioSource", "AudioChunk", "SyntheticAudioSource", "WavFileAudioSource"]
