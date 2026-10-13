"""Captura de audio.

Contrato: ``AudioSource`` entrega :class:`AudioChunk` mono ``float32``.
"""

from app.audio.base import AudioSource
from app.audio.capture import (
    AudioDeviceInfo,
    MicrophoneAudioSource,
    SystemAudioSource,
    find_monitor_devices,
    list_audio_devices,
)
from app.audio.models import AudioChunk
from app.audio.synthetic import SyntheticAudioSource
from app.audio.wav import WavFileAudioSource

__all__ = [
    "AudioSource",
    "AudioChunk",
    "AudioDeviceInfo",
    "MicrophoneAudioSource",
    "SyntheticAudioSource",
    "SystemAudioSource",
    "WavFileAudioSource",
    "find_monitor_devices",
    "list_audio_devices",
]
