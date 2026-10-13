"""Fase 2: captura del sistema con mocks (sin hardware)."""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from app.audio.capture import (
    AudioDeviceInfo,
    MicrophoneAudioSource,
    SystemAudioSource,
    find_monitor_devices,
    resample_mono,
)
from app.core.errors import AudioError


def _dev(index, name, *, kind="monitor", ch_in=2, monitor=True):
    return AudioDeviceInfo(
        index=index,
        name=name,
        kind=kind,
        channels_in=ch_in,
        channels_out=2,
        default_rate=48000.0,
        is_monitor=monitor,
    )


def test_find_monitor_devices_prefers_pactl_hit():
    plain = _dev(13, "Raptor Lake-P/U/H cAVS Speaker, JACK")
    preferred = AudioDeviceInfo(
        index=10,
        name="default output",
        kind="monitor",
        channels_in=2,
        channels_out=2,
        default_rate=48000.0,
        is_monitor=True,
        is_default=True,
        pactl_name="alsa_output.Speaker__sink.monitor",
    )
    result = find_monitor_devices([plain, preferred])
    assert result[0] == preferred
    assert all(d.is_monitor for d in result)


def test_resample_mono_48k_to_16k():
    src = np.ones(4800, dtype=np.float32)
    out = resample_mono(src, 48_000, 16_000)
    assert out.dtype == np.float32
    assert out.shape == (1600,)
    assert out == pytest.approx(np.ones(1600, dtype=np.float32))


def test_resample_mono_same_rate_passthrough():
    src = np.arange(8, dtype=np.float32)
    assert resample_mono(src, 16_000, 16_000).shape == (8,)


class _FakeStream:
    def __init__(self, *args, **kwargs):
        self.callback = kwargs.get("callback")
        self.started = False

    def start(self):
        self.started = True
        # Simula 48 kHz estéreo: 4800 frames = 0.1 s.
        stereo = np.ones((4800, 2), dtype=np.float32) * 0.5
        self.callback(stereo, 4800, None, None)

    def stop(self):
        self.started = False

    def close(self):
        pass


def _install_fake_sounddevice(monkeypatch, devices):
    fake = types.ModuleType("sounddevice")
    fake.InputStream = _FakeStream
    fake.query_devices = lambda: devices
    fake.default = types.SimpleNamespace(device=(0, 0))
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    return fake


def test_system_source_downmix_and_resample(monkeypatch):
    from app.audio import capture as cap

    _install_fake_sounddevice(
        monkeypatch,
        [
            {"name": "Speaker JACK", "max_input_channels": 2, "max_output_channels": 2,
             "default_samplerate": 48000.0},
        ],
    )
    monkeypatch.setattr(cap, "list_audio_devices", lambda *a, **k: [
        _dev(0, "Speaker JACK"),
    ])
    src = SystemAudioSource(None, target_rate=16_000, chunk_duration=0.1)
    src.start_capture()
    chunk = src.read_audio_chunk(timeout=1.0)
    src.stop_capture()
    assert chunk is not None
    assert chunk.sample_rate == 16_000
    assert chunk.samples.dtype == np.float32
    assert chunk.samples.ndim == 1
    # 4800 frames @48k estéreo → 0.1 s @16k mono = 1600 muestras.
    assert chunk.samples.shape[0] == 1600
    assert chunk.samples == pytest.approx(np.full(1600, 0.5, dtype=np.float32))


def test_read_timeout_returns_none_without_data(monkeypatch):
    from app.audio import capture as cap

    class _SilentStream(_FakeStream):
        def start(self):
            self.started = True  # no invoca callback: cola vacía

    fake = types.ModuleType("sounddevice")
    fake.InputStream = _SilentStream
    fake.query_devices = lambda: []
    fake.default = types.SimpleNamespace(device=(0, 0))
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    monkeypatch.setattr(cap, "list_audio_devices", lambda *a, **k: [_dev(0, "Speaker JACK")])
    src = SystemAudioSource(None, target_rate=16_000, chunk_duration=0.5)
    src.start_capture()
    try:
        assert src.read_audio_chunk(timeout=0.05) is None
        assert src.running is True  # fuente infinita: timeout ≠ fin
    finally:
        src.stop_capture()
    assert src.running is False


def test_stop_idempotente(monkeypatch):
    from app.audio import capture as cap

    _install_fake_sounddevice(monkeypatch, [])
    monkeypatch.setattr(cap, "list_audio_devices", lambda *a, **k: [_dev(0, "Speaker JACK")])
    src = SystemAudioSource(None)
    src.start_capture()
    src.stop_capture()
    src.stop_capture()  # no debe lanzar


def test_microphone_auto_excluye_monitores():
    from app.audio.capture import _resolve_device

    devs = [
        _dev(0, "Speaker JACK", kind="monitor", monitor=True),
        _dev(1, "Digital Microphone JACK", kind="microphone", monitor=False),
    ]
    chosen = _resolve_device(None, want_monitor=False, devices=devs)
    assert chosen.index == 1
    with pytest.raises(AudioError):
        _resolve_device(None, want_monitor=True, devices=[devs[1]])


def test_factory_registra_monitor_y_microphone():
    from app.core.factory import build_audio_source
    from app.settings.config import AppSettings

    s = AppSettings()
    s.audio.source = "monitor"
    assert isinstance(build_audio_source(s), SystemAudioSource)
    s.audio.source = "microphone"
    assert isinstance(build_audio_source(s), MicrophoneAudioSource)
