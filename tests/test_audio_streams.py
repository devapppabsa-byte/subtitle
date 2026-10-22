"""Tests del enrutado de audio por aplicación/pestaña (Fase 10).

No tocan el servidor de audio real: el ejecutor de comandos es inyectable.
"""

from __future__ import annotations

import subprocess
import types

import pytest

from app.audio.streams import (
    AudioStreamInfo,
    StreamRouter,
    list_audio_streams,
    parse_sink_inputs_json,
    parse_sink_inputs_text,
)


def _cp(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["pactl"], returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# Parseo
# ---------------------------------------------------------------------------
JSON_PAYLOAD = """[
  {
    "index": 12,
    "driver": "Protocol native",
    "owner_module": 23,
    "client": 42,
    "sink": 0,
    "sample_specification": "float32le 2ch 48000Hz",
    "channel_map": "front-left,front-right",
    "properties": {
      "media.name": "Playing music",
      "media.title": "Behind The Scenes",
      "application.name": "Chromium",
      "application.id": "Chromium",
      "application.process.binary": "chromium",
      "application.process.id": "1234"
    }
  }
]"""


def test_parse_sink_inputs_json_reads_app_and_title():
    streams = parse_sink_inputs_json(JSON_PAYLOAD)
    assert len(streams) == 1
    stream = streams[0]
    assert stream.index == 12
    assert stream.app == "Chromium"
    assert stream.title == "Behind The Scenes"
    assert stream.binary == "chromium"
    assert stream.sink == 0
    assert stream.spec == "chromium::Behind The Scenes"
    assert "Chromium" in stream.label and "Behind The Scenes" in stream.label


def test_parse_sink_inputs_json_tolerates_garbage():
    assert parse_sink_inputs_json("no json") == []
    assert parse_sink_inputs_json("[]") == []
    assert parse_sink_inputs_json('{"index": "x"}') == []


TEXT_PAYLOAD = """Sink Input #7
\tDriver: protocol-native.c
\tSink: 0
\tSample Specification: float32le 2ch 48000Hz
\tProperties:
\t\tapplication.name = "Spotify"
\t\tmedia.title = "Song"
\t\tapplication.process.binary = "spotify"

Sink Input #9
\tSink: 0
\tProperties:
\t\tapplication.name = "Firefox"
\t\tmedia.title = "Podcast"
"""


def test_parse_sink_inputs_text_reads_blocks():
    streams = parse_sink_inputs_text(TEXT_PAYLOAD)
    assert [s.index for s in streams] == [7, 9]
    assert streams[0].app == "Spotify"
    assert streams[0].title == "Song"
    assert streams[1].app == "Firefox"
    assert streams[1].title == "Podcast"


def test_parse_sink_inputs_text_ignores_garbage():
    assert parse_sink_inputs_text("") == []
    assert parse_sink_inputs_text("Sink Input #x") == []


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
def test_matches_app_and_title():
    stream = AudioStreamInfo(index=1, app="Chromium", title="YouTube", binary="chromium")
    assert stream.matches("chromium::YouTube")
    assert stream.matches("chromium")
    assert not stream.matches("chromium::Spotify")
    assert not stream.matches("spotify::YouTube")
    assert not stream.matches("")


def test_matches_partial_title():
    stream = AudioStreamInfo(index=1, app="Chromium", title="Café con leche - YouTube", binary="chromium")
    assert stream.matches("chromium::youtube")


# ---------------------------------------------------------------------------
# Listado
# ---------------------------------------------------------------------------
def test_list_audio_streams_uses_json_first():
    calls = []

    def runner(args):
        calls.append(list(args))
        if "--format=json" in args:
            return _cp(JSON_PAYLOAD)
        return _cp("")

    streams = list_audio_streams(runner=runner)
    assert len(streams) == 1
    assert streams[0].app == "Chromium"
    assert calls[0][:2] == ["pactl", "--format=json"]


def test_list_audio_streams_falls_back_to_text():
    def runner(args):
        if "--format=json" in args:
            return _cp("[]")
        return _cp(TEXT_PAYLOAD)

    streams = list_audio_streams(runner=runner)
    assert [s.app for s in streams] == ["Firefox", "Spotify"]


def test_list_audio_streams_empty_when_nothing_plays():
    def runner(args):
        return _cp("[]" if "--format=json" in args else "")

    assert list_audio_streams(runner=runner) == []


def test_list_audio_streams_survives_errors():
    def runner(args):
        raise OSError("pactl no está")

    assert list_audio_streams(runner=runner) == []


def test_list_sink_map_reads_index_and_name():
    from app.audio.streams import list_sink_map

    def runner(args):
        return _cp("51\talsa_output.hdmi\tPipeWire\ts24le 2ch 48000Hz\n52\tsubtitle_capture\tPipeWire\tfloat32le 2ch 48000Hz")

    assert list_sink_map(runner=runner) == {51: "alsa_output.hdmi", 52: "subtitle_capture"}


def test_list_audio_streams_resolves_sink_name():
    """El JSON no trae el nombre del sink: se resuelve con la tabla de sinks."""

    def runner(args):
        if "--format=json" in args:
            return _cp(JSON_PAYLOAD)
        if "short" in args and "sinks" in args:
            return _cp("0\talsa_output.speaker\tPipeWire\tfloat32le 2ch 48000Hz")
        return _cp("")

    streams = list_audio_streams(runner=runner)
    assert streams[0].sink_name == "alsa_output.speaker"


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
class FakeServer:
    """Servidor de audio simulado: registra los comandos de pactl.

    Reproduce el comportamiento real de ``pactl``:

    * ``list short sinks`` devuelve ``INDICE\\tNOMBRE\\t...``;
    * el JSON de ``sink-inputs`` trae el **índice** del sink, no su nombre;
    * ``move-sink-input`` acepta un nombre o un índice de sink.
    """

    def __init__(self, sinks=None, default_sink="alsa_output.speaker") -> None:
        self.calls: list[list[str]] = []
        self.sinks: dict[int, str] = dict(
            sinks if sinks is not None else {0: "alsa_output.speaker", 99: "subtitle_capture"}
        )
        self.default_sink = default_sink
        self.streams: list[AudioStreamInfo] = []
        #: sink input índice → índice de sink actual
        self.sink_of: dict[int, int] = {}
        #: sink input índice → nombre de sink solicitado en el último movimiento
        self.moved: dict[int, str] = {}

    def _resolve_sink(self, target: str) -> int:
        for index, name in self.sinks.items():
            if name == target:
                return index
        try:
            return int(target)
        except ValueError:
            return 0

    def runner(self, args):
        args = list(args)
        self.calls.append(args)
        positional = [a for a in args[1:] if not a.startswith("-")]
        cmd = positional[0] if positional else ""
        if cmd == "load-module":
            return _cp("42")
        if cmd == "list" and "short" in args and "sinks" in args:
            return _cp("\n".join(f"{i}\t{name}\tPipeWire\tfloat32le 2ch 48000Hz" for i, name in self.sinks.items()))
        if cmd == "list" and "short" in args:
            return _cp("")
        if cmd == "get-default-sink":
            return _cp(self.default_sink)
        if cmd == "move-sink-input":
            index = int(positional[1])
            target = positional[2]
            self.moved[index] = target
            self.sink_of[index] = self._resolve_sink(target)
            return _cp()
        if cmd == "unload-module":
            return _cp()
        if cmd == "list" and "sink-inputs" in args:
            import json

            payload = [
                {
                    "index": s.index,
                    "sink": self.sink_of.get(s.index, 0),
                    "properties": {"application.name": s.app, "media.title": s.title},
                }
                for s in self.streams
            ]
            return _cp(json.dumps(payload))
        return _cp()


def test_router_creates_null_sink_and_routes_stream():
    server = FakeServer(sinks={})
    router = StreamRouter(runner=server.runner)
    router.ensure_null_sink()
    assert any("module-null-sink" in " ".join(call) for call in server.calls)


def test_router_moves_matching_streams():
    server = FakeServer()
    server.streams = [
        AudioStreamInfo(index=3, app="Chromium", title="YouTube", binary="chromium"),
        AudioStreamInfo(index=4, app="Spotify", title="Song", binary="spotify"),
    ]
    router = StreamRouter(runner=server.runner)
    moved = router.route_matching("chromium::YouTube")
    assert moved == [3]
    assert server.moved[3] == "subtitle_capture"
    assert 4 not in server.moved


def test_router_enables_loopback_to_default_sink():
    server = FakeServer()
    router = StreamRouter(runner=server.runner)
    assert router.enable_loopback() is True
    loopback = [c for c in server.calls if "module-loopback" in c]
    assert loopback, "debería cargar module-loopback"
    joined = " ".join(loopback[0])
    assert "source=subtitle_capture.monitor" in joined
    assert "sink=alsa_output.speaker" in joined


def test_router_restores_streams_to_original_sink():
    """El stream debe volver al sink ORIGINAL, no al virtual."""
    server = FakeServer()
    server.streams = [AudioStreamInfo(index=3, app="Chromium", title="YouTube", binary="chromium")]
    server.sink_of[3] = 0  # estaba en el altavoz
    router = StreamRouter(runner=server.runner)
    router.route_matching("chromium::YouTube")
    assert server.sink_of[3] == 99  # movido al sink virtual
    router.enable_loopback()
    router.restore()
    assert server.moved[3] == "alsa_output.speaker"
    assert server.sink_of[3] == 0


def test_router_restores_streams_and_unloads_modules():
    server = FakeServer()
    server.streams = [AudioStreamInfo(index=3, app="Chromium", title="YouTube", binary="chromium")]
    router = StreamRouter(runner=server.runner)
    router.route_matching("chromium::YouTube")
    router.enable_loopback()
    server.calls.clear()
    router.restore()
    joined = " ".join(" ".join(c) for c in server.calls)
    assert "unload-module" in joined
    assert "move-sink-input 3" in joined


def test_router_does_not_route_same_stream_twice():
    """El vigilante no debe reenrutar lo ya movido (log y ruido)."""
    server = FakeServer()
    server.streams = [AudioStreamInfo(index=3, app="Chromium", title="YouTube", binary="chromium")]
    router = StreamRouter(runner=server.runner)
    assert router.route_matching("chromium::YouTube") == [3]
    assert router.route_matching("chromium::YouTube") == []


def test_router_capture_source_name():
    router = StreamRouter(sink_name="mi_captura", runner=FakeServer().runner)
    assert router.capture_source == "mi_captura.monitor"


def test_router_cleanup_orphans_returns_streams():
    """Los streams atrapados en el sink virtual vuelven al altavoz."""
    server = FakeServer()
    server.streams = [AudioStreamInfo(index=8, app="Chromium", title="X", binary="chromium")]
    server.sink_of[8] = 99  # quedó atrapado en el sink virtual
    router = StreamRouter(runner=server.runner)
    assert router.cleanup_orphans() == 1
    assert server.moved[8] == "alsa_output.speaker"


def test_router_cleanup_orphans_ignores_other_sinks():
    server = FakeServer()
    server.streams = [AudioStreamInfo(index=8, app="Chromium", title="X", binary="chromium")]
    server.sink_of[8] = 0  # está en el altavoz: no hay que tocarlo
    router = StreamRouter(runner=server.runner)
    assert router.cleanup_orphans() == 0
    assert 8 not in server.moved


def test_router_without_pactl_does_not_raise():
    def runner(args):
        raise subprocess.SubprocessError("boom")

    router = StreamRouter(runner=runner)
    assert router.enable_loopback() is False
    router.restore()  # no debe lanzar


# ---------------------------------------------------------------------------
# Fuente de aplicación
# ---------------------------------------------------------------------------
class FakeRouter:
    """Enrutador simulado: registra lo que la fuente le pide."""

    sink_name = "subtitle_capture"
    capture_source = "subtitle_capture.monitor"

    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.matched: list[str] = []
        self.loopback = False
        self.watching: list[str] = []
        self.restored = False

    def ensure_null_sink(self) -> bool:
        return self.available

    def cleanup_orphans(self) -> int:
        return 0

    def route_matching(self, spec: str) -> list[int]:
        self.matched.append(spec)
        return [3] if self.available else []

    def enable_loopback(self) -> bool:
        self.loopback = True
        return True

    def start_watching(self, spec: str) -> None:
        self.watching.append(spec)

    def restore(self) -> None:
        self.restored = True


def test_app_source_routes_on_start_and_restores_on_stop(monkeypatch):
    from app.audio.app_source import AppStreamAudioSource

    monkeypatch.setattr(AppStreamAudioSource, "_start_capture", lambda self: None)
    monkeypatch.setattr(AppStreamAudioSource, "_stop_capture", lambda self: None)

    router = FakeRouter()
    source = AppStreamAudioSource("chromium::YouTube", router=router)
    source.start()
    assert router.matched == ["chromium::YouTube"]
    assert router.loopback is True
    assert router.watching == ["chromium::YouTube"]
    source.stop()
    assert router.restored is True


def test_app_source_cleans_orphans_on_start(monkeypatch):
    """Al arrancar se rescatan los streams que quedaron en el sink virtual."""
    from app.audio.app_source import AppStreamAudioSource

    monkeypatch.setattr(AppStreamAudioSource, "_start_capture", lambda self: None)
    monkeypatch.setattr(AppStreamAudioSource, "_stop_capture", lambda self: None)

    router = FakeRouter()
    router.orphans_cleaned = 0
    router.cleanup_orphans = lambda: setattr(router, "orphans_cleaned", router.orphans_cleaned + 1) or 0
    source = AppStreamAudioSource("chromium::YouTube", router=router)
    source.start()
    assert router.orphans_cleaned == 1
    source.stop()


def test_app_source_without_null_sink_raises(monkeypatch):
    from app.audio.app_source import AppStreamAudioSource
    from app.core.errors import AudioError

    monkeypatch.setattr(AppStreamAudioSource, "_start_capture", lambda self: None)
    router = FakeRouter(available=False)
    source = AppStreamAudioSource("chromium::YouTube", router=router)
    with pytest.raises(AudioError):
        source.start()


def test_app_source_can_skip_loopback(monkeypatch):
    from app.audio.app_source import AppStreamAudioSource

    monkeypatch.setattr(AppStreamAudioSource, "_start_capture", lambda self: None)
    monkeypatch.setattr(AppStreamAudioSource, "_stop_capture", lambda self: None)

    router = FakeRouter()
    source = AppStreamAudioSource("chromium::YouTube", loopback=False, router=router)
    source.start()
    assert router.loopback is False
    source.stop()


def test_app_source_reads_mono_float32(monkeypatch):
    """Los chunks entregados al pipeline son mono float32 a la tasa pedida."""
    import numpy as np

    from app.audio.app_source import AppStreamAudioSource

    monkeypatch.setattr(AppStreamAudioSource, "_start_capture", lambda self: None)
    monkeypatch.setattr(AppStreamAudioSource, "_stop_capture", lambda self: None)

    source = AppStreamAudioSource("chromium::YouTube", router=FakeRouter(), chunk_duration=0.1)
    source.start()
    pcm = (np.sin(np.linspace(0, 6.28, 1600)) * 16000).astype("<i2")
    source._queue.put(pcm.astype(np.float32) / 32768.0)
    chunk = source.read(timeout=1.0)
    assert chunk is not None
    assert chunk.samples.dtype == np.float32
    assert chunk.samples.ndim == 1
    assert chunk.sample_rate == 16000
    source.stop()


def test_app_source_start_time_is_relative_to_stream(monkeypatch):
    """``start_time`` es relativo al inicio (igual que la fuente del sistema)."""
    import numpy as np

    from app.audio.app_source import AppStreamAudioSource

    monkeypatch.setattr(AppStreamAudioSource, "_start_capture", lambda self: None)
    monkeypatch.setattr(AppStreamAudioSource, "_stop_capture", lambda self: None)

    source = AppStreamAudioSource("chromium::YouTube", router=FakeRouter(), chunk_duration=0.1)
    source.start()
    block = np.zeros(1600, dtype=np.float32)
    source._queue.put(block)
    first = source.read(timeout=1.0)
    source._queue.put(block)
    second = source.read(timeout=1.0)
    assert first is not None and second is not None
    assert first.start_time == 0.0
    assert second.start_time == pytest.approx(0.1)
    source.stop()


def test_app_source_uses_parec_with_mono_16k(monkeypatch):
    """La captura pide mono s16le a 16 kHz (formato del reconocedor)."""
    from app.audio import app_source

    captured: list[list[str]] = []

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            captured.append(list(cmd))
            self.stdout = None
            self.returncode = None
            self.poll = lambda: None
            self.terminate = lambda: None
            self.wait = lambda timeout=None: 0
            self.kill = lambda: None

    monkeypatch.setattr(app_source.shutil, "which", lambda name: "/usr/bin/parec")
    monkeypatch.setattr(app_source.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(app_source.time, "sleep", lambda _: None)

    source = app_source.AppStreamAudioSource("chromium::YouTube", router=FakeRouter())
    source._start_capture()
    cmd = captured[0]
    assert cmd[0] == "parec"
    assert "--device=subtitle_capture.monitor" in cmd
    assert "--format=s16le" in cmd
    assert "--rate=16000" in cmd
    assert "--channels=1" in cmd
