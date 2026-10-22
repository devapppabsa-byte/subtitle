"""Fase 6: ventanas Qt por idioma (offscreen, sin display)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from app.settings.config import AppSettings
from app.subtitles.models import SubtitleCue, SubtitleStyle
from app.ui.styles import badge_stylesheet, button_stylesheet, subtitle_stylesheet


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("subtitle-app-test")
    yield app


def _cue(text, target, *, start=0.0, source="hello", final=True):
    return SubtitleCue(
        cue_id=f"u0:{target}", text=text, is_final=final, start_time=start,
        end_time=start + 0.5, source_text=source, target_language=target,
    )


# -- styles (puro, sin Qt) ---------------------------------------------------
def test_stylesheets_contain_size_and_colors():
    css = subtitle_stylesheet(SubtitleStyle(font_size=40))
    assert "font-size: 40px" in css and "color:" in css and "border-radius" in css
    assert "ES" not in badge_stylesheet("es")  # la etiqueta va en el QLabel, no en el css
    assert "#F28B82" in badge_stylesheet("es")
    assert "QToolButton" in button_stylesheet()


# -- SubtitleWindow ----------------------------------------------------------
def test_windows_show_only_their_language(qapp):
    from app.ui.subtitle_window import create_subtitle_window

    es = create_subtitle_window("es")
    fr = create_subtitle_window("fr")
    try:
        es.update_subtitle("hola mundo")
        fr.update_subtitle("bonjour le monde")
        assert es.last_text == "hola mundo"
        assert fr.last_text == "bonjour le monde"
        assert "bonjour" not in es.last_text and "hola" not in fr.last_text
    finally:
        es.hide()
        fr.hide()


def test_partial_does_not_erase_final(qapp):
    from app.ui.subtitle_window import create_subtitle_window

    window = create_subtitle_window("es")
    try:
        window.update_subtitle("hola mundo")
        window.update_subtitle("…hola", is_final=False)
        assert window.last_text == "hola mundo"
    finally:
        window.hide()


def test_drag_moves_and_lock_freezes(qapp):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from app.ui.subtitle_window import create_subtitle_window

    window = create_subtitle_window("es")
    try:
        window.widget.move(100, 100)
        start = window.widget.pos().toTuple()
        QTest.mousePress(window.widget, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
        QTest.mouseMove(window.widget, QPoint(60, 40))
        QTest.mouseRelease(window.widget, Qt.MouseButton.LeftButton, pos=QPoint(60, 40))
        assert window.widget.pos().toTuple() != start

        window.set_locked(True)
        frozen = window.widget.pos().toTuple()
        QTest.mousePress(window.widget, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
        QTest.mouseMove(window.widget, QPoint(90, 90))
        QTest.mouseRelease(window.widget, Qt.MouseButton.LeftButton, pos=QPoint(90, 90))
        assert window.widget.pos().toTuple() == frozen
    finally:
        window.hide()


def test_opacity_top_and_resize(qapp):
    from PySide6.QtCore import Qt

    from app.ui.subtitle_window import create_subtitle_window

    window = create_subtitle_window("de")
    try:
        window.set_window_opacity(0.5)
        # Offscreen cuantiza la opacidad ("does not support setting window opacity").
        assert window.widget.windowOpacity() == pytest.approx(0.5, abs=0.05)
        window.toggle_always_on_top(False)
        assert not (window.widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        window.toggle_always_on_top(True)
        assert window.widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
        window.widget.resize(600, 200)
        assert window.widget.size().toTuple() == (600, 200)
    finally:
        window.hide()


def test_geometry_roundtrip_in_temp_settings(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    import app.ui.subtitle_window as mod

    monkeypatch.setattr(
        mod, "_settings", lambda lang: QSettings(str(tmp_path / f"{lang}.ini"), QSettings.Format.IniFormat)
    )
    window = mod.SubtitleWindow("xx-test-lang")
    try:
        window.widget.move(123, 45)
        window.widget.resize(500, 160)
        window.save_window_geometry()
        window.widget.move(0, 0)
        window.restore_geometry()
        assert window.widget.pos().toTuple() == (123, 45)
    finally:
        window.hide()


# -- MainWindow --------------------------------------------------------------
def _main_window(qapp):
    from app.audio.synthetic import SyntheticAudioSource
    from app.core.pipeline import SubtitlePipeline
    from app.speech.scripted import ScriptedSpeechRecognizer
    from app.translation.passthrough import PassthroughTranslator
    from app.ui.bridge import PipelineBridge
    from app.ui.main_window import MainWindow

    settings = AppSettings()
    settings.translation.target_languages = ["es", "fr"]
    pipeline = SubtitlePipeline(
        audio_source=SyntheticAudioSource(total_duration=1.0),
        recognizer=ScriptedSpeechRecognizer([]),
        translator=PassthroughTranslator(),
        source_language="en",
        target_languages=["es", "fr"],
    )
    bridge = PipelineBridge(pipeline, None)
    return MainWindow(settings, pipeline, bridge)


def test_main_routes_each_language_exclusively(qapp):
    main = _main_window(qapp)
    main._route_cue(_cue("hola", "es", source="hello"))
    main._route_cue(_cue("bonjour", "fr", source="hello"))
    windows = main.windows
    assert set(windows) == {"original", "es", "fr"}
    assert windows["es"].last_text == "hola"
    assert windows["fr"].last_text == "bonjour"
    assert windows["original"].last_text == "hello"
    for window in windows.values():
        window.hide()


def test_toggle_language_hides_and_recreates(qapp):
    main = _main_window(qapp)
    main._route_cue(_cue("hola", "es"))
    assert "es" in main.windows
    main._on_language_toggled("es", False)
    assert "es" not in main.windows
    main._on_language_toggled("es", True)
    assert "es" in main.windows
    for window in main.windows.values():
        window.hide()


def test_font_and_opacity_apply_to_all(qapp):
    main = _main_window(qapp)
    main.update_subtitle("es", "hola")
    main.update_subtitle("fr", "bonjour")
    main._on_font_changed(44)
    main._on_opacity_changed(60)
    for window in main.windows.values():
        assert window.widget is not None
    assert main._settings.overlay.style.font_size == 44
    for window in main.windows.values():
        window.hide()


def test_settings_dialog_applies(qapp):
    from app.ui.settings_dialog import SettingsDialog

    settings = AppSettings()
    applied = []
    dialog = SettingsDialog(settings, on_apply=lambda: applied.append(True))
    dialog._apply("fr, de", 44, 80, True)
    assert settings.translation.target_languages == ["fr", "de"]
    assert settings.translation.target_language == "fr"
    assert settings.overlay.style.font_size == 44
    assert settings.overlay.style.background_opacity == pytest.approx(0.8)
    assert settings.overlay.always_on_top is True
    assert applied == [True]


def test_bridge_router_called_instead_of_renderer(qapp):
    from app.ui.bridge import PipelineBridge

    received = []
    pipeline = _main_window(qapp)._pipeline
    bridge = PipelineBridge(pipeline, None)
    bridge.set_router(received.append)
    cue = _cue("hola", "es")
    bridge._on_cue(cue)
    assert received == [cue]


def test_finished_keeps_panel_open(qapp):
    """El fin de una fuente finita no cierra la app (bug del lanzador)."""
    main = _main_window(qapp)
    main.toggle_running()
    assert main._running is True
    main.on_pipeline_finished("completed")
    assert main._running is False
    assert main._toggle.text() == "Iniciar"
    assert "Finalizado" in main._status.text()
    for window in main.windows.values():
        window.hide()


def test_finished_is_idempotent(qapp):
    """Un segundo ``finished`` no re-arranca ni cambia el estado."""
    main = _main_window(qapp)
    main.toggle_running()
    main.on_pipeline_finished("completed")
    state = (main._running, main._toggle.text(), main._status.text())
    main.on_pipeline_finished("completed")
    assert (main._running, main._toggle.text(), main._status.text()) == state
    assert main._running is False
    for window in main.windows.values():
        window.hide()


# -- sección "Origen de audio" ------------------------------------------------
def _streams():
    from app.audio.streams import AudioStreamInfo

    return [
        AudioStreamInfo(index=3, app="Chromium", title="YouTube", binary="chromium"),
        AudioStreamInfo(index=4, app="Spotify", title="Song", binary="spotify"),
    ]


def test_audio_section_lists_streams(qapp):
    """El botón Actualizar rellena el combo con las apps detectadas."""
    main = _main_window(qapp)
    main._streams_fetcher = _streams
    main._on_refresh_streams()
    main._finish_streams(_streams(), "")
    assert main._stream_combo.count() == 2
    assert "Chromium" in main._stream_combo.itemText(0)
    assert main._stream_combo.itemData(0) == "chromium::YouTube"


def test_audio_section_reports_no_streams(qapp):
    main = _main_window(qapp)
    main._finish_streams([], "")
    assert main._stream_combo.count() == 0
    assert "No hay ninguna app" in main._audio_status.text()


def test_audio_section_app_origin_enables_stream_combo(qapp):
    main = _main_window(qapp)
    index = main._origin_combo.findData("app")
    main._origin_combo.setCurrentIndex(index)
    assert main._stream_combo.isEnabled() is True
    main._origin_combo.setCurrentIndex(main._origin_combo.findData("monitor"))
    assert main._stream_combo.isEnabled() is False


def test_apply_origin_rebuilds_source(qapp):
    """Aplicar un origen nuevo cambia la fuente del pipeline."""
    from app.audio.synthetic import SyntheticAudioSource

    main = _main_window(qapp)
    main._origin_combo.setCurrentIndex(main._origin_combo.findData("synthetic"))
    main._on_apply_origin()
    assert main._settings.audio.source == "synthetic"
    assert isinstance(main._pipeline.audio_source, SyntheticAudioSource)


def test_apply_origin_app_requires_stream(qapp):
    main = _main_window(qapp)
    main._origin_combo.setCurrentIndex(main._origin_combo.findData("app"))
    main._finish_streams([], "")
    main._on_apply_origin()
    assert "Elegí una aplicación" in main._audio_status.text()


def test_apply_origin_keeps_config_when_source_is_invalid(qapp, monkeypatch):
    """Si la fuente no se puede construir, la config queda como estaba."""
    import app.core.factory as factory

    main = _main_window(qapp)
    main._origin_combo.setCurrentIndex(main._origin_combo.findData("synthetic"))
    main._on_apply_origin()
    assert main._settings.audio.source == "synthetic"

    def boom(settings):
        raise RuntimeError("no se puede")

    monkeypatch.setattr(factory, "build_audio_source", boom)
    main._origin_combo.setCurrentIndex(main._origin_combo.findData("monitor"))
    main._on_apply_origin()
    assert main._settings.audio.source == "synthetic"
    assert "No se pudo preparar" in main._audio_status.text()


def test_apply_origin_uses_selected_stream(qapp):
    from app.audio.app_source import AppStreamAudioSource

    main = _main_window(qapp)
    main._finish_streams(_streams(), "")
    main._origin_combo.setCurrentIndex(main._origin_combo.findData("app"))
    main._on_apply_origin()
    assert main._settings.audio.source == "app"
    assert main._settings.audio.stream_spec == "chromium::YouTube"
    assert isinstance(main._pipeline.audio_source, AppStreamAudioSource)
    assert main._pipeline.audio_source.spec == "chromium::YouTube"


def test_apply_origin_app_starts_and_restores_router(qapp, monkeypatch):
    """Aplicar el origen 'app' enruta el stream y al parar lo devuelve."""
    from app.audio import app_source

    events: list[str] = []

    class FakeRouter:
        sink_name = "subtitle_capture"
        capture_source = "subtitle_capture.monitor"

        def ensure_null_sink(self):
            events.append("ensure")
            return True

        def route_matching(self, spec):
            events.append(f"route:{spec}")
            return [3]

        def enable_loopback(self):
            events.append("loopback")
            return True

        def start_watching(self, spec):
            events.append(f"watch:{spec}")

        def restore(self):
            events.append("restore")

    monkeypatch.setattr(app_source, "StreamRouter", FakeRouter)
    monkeypatch.setattr(app_source.AppStreamAudioSource, "_start_capture", lambda self: None)
    monkeypatch.setattr(app_source.AppStreamAudioSource, "_stop_capture", lambda self: None)

    main = _main_window(qapp)
    main._finish_streams(_streams(), "")
    main._origin_combo.setCurrentIndex(main._origin_combo.findData("app"))
    main._on_apply_origin()
    assert main._settings.audio.source == "app"

    main.toggle_running()  # arranca el pipeline con la fuente de app
    assert main._running is True
    assert "ensure" in events
    assert "route:chromium::YouTube" in events
    assert "loopback" in events
    assert "watch:chromium::YouTube" in events

    main.toggle_running()  # lo para: debe restaurar los streams
    assert "restore" in events
    for window in main.windows.values():
        window.hide()


def test_apply_origin_keeps_running(qapp):
    """Cambiar el origen con la app corriendo la deja corriendo."""
    main = _main_window(qapp)
    main.toggle_running()
    assert main._running is True
    main._origin_combo.setCurrentIndex(main._origin_combo.findData("synthetic"))
    main._on_apply_origin()
    assert main._running is True
    main.toggle_running()
    for window in main.windows.values():
        window.hide()


def test_qt_defaults_synthetic_to_monitor(qapp):
    """Sin --audio, el arranque gráfico no usa el demo sintético finito."""
    import main as entry

    settings = _main_window(qapp)._settings
    settings.audio.source = "synthetic"
    settings.overlay.renderer = "qt"

    args = entry.build_parser().parse_args(["--renderer", "qt"])
    entry.use_qt_default_source(settings, args)
    assert settings.audio.source == "monitor"

    settings.audio.source = "synthetic"
    demo = entry.build_parser().parse_args(["--renderer", "qt", "--audio", "synthetic"])
    entry.use_qt_default_source(settings, demo)
    assert settings.audio.source == "synthetic"


def test_file_logging_writes(tmp_path):
    import logging

    from app.core.logging import configure_file_logging, configure_logging

    configure_logging(logging.INFO)
    target = configure_file_logging(str(tmp_path / "sub" / "app.log"))
    assert target.endswith("app.log")
    logging.getLogger("subtitle_app.test_probe").info("hola-log")
    assert "hola-log" in (tmp_path / "sub" / "app.log").read_text()


def test_ai_section_applies_provider(qapp, tmp_path, monkeypatch, fake_vault):
    """Validar un proveedor guarda la clave cifrada, cambia engine y hace swap."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    main = _main_window(qapp)
    swapped = []
    main._on_engine_change = swapped.append

    from app.settings.store import ValidationReport

    class _Validator:
        def __call__(self, provider_id, api_key):
            return ValidationReport(
                provider=provider_id, key_env="OPENAI_API_KEY",
                key_present=True, ok=True, detail="2 modelos visibles",
            )

    main._validator = _Validator()
    main._models_fetcher = lambda pid, key: []
    index = main._engine_combo.findData("openai")
    main._engine_combo.setCurrentIndex(index)
    main._key_edit.setText("sk-fake-key")
    main._on_validate_clicked()
    import time

    for _ in range(50):
        if swapped:
            break
        qapp.processEvents()
        time.sleep(0.02)
    assert swapped, "no se aplicó el proveedor"
    assert main._settings.translation.engine == "openai"
    assert fake_vault.load_key("openai") == "sk-fake-key"
    # La clave NUNCA está en claro en la BD.
    assert b"sk-fake-key" not in (tmp_path / "keys.db").read_bytes()
    for window in main.windows.values():
        window.hide()


def test_ai_section_rejects_invalid_key(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    main = _main_window(qapp)

    from app.settings.store import ValidationReport

    main._validator = lambda pid, key: ValidationReport(
        provider=pid, key_env="ANTHROPIC_API_KEY", key_present=True, ok=False,
        detail="HTTP 401: clave inválida",
    )
    index = main._engine_combo.findData("anthropic")
    main._engine_combo.setCurrentIndex(index)
    main._key_edit.setText("sk-ant-mala")
    main._on_validate_clicked()
    import time

    for _ in range(50):
        qapp.processEvents()
        if "401" in main._ai_status.text():
            break
        time.sleep(0.02)
    assert "401" in main._ai_status.text()
    assert main._settings.translation.engine != "anthropic"
    for window in main.windows.values():
        window.hide()


def test_ai_section_fills_models_combo(qapp, tmp_path, monkeypatch):
    """Al validar, el combo de modelos se rellena con los modelos del proveedor."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    main = _main_window(qapp)
    main._on_engine_change = lambda t: None

    from app.settings.store import ValidationReport
    from app.translation.compat import ModelInfo

    main._validator = lambda pid, key: ValidationReport(
        provider=pid, key_env="OPENROUTER_API_KEY", key_present=True, ok=True, detail="ok",
    )
    main._models_fetcher = lambda pid, key: [
        ModelInfo(id="nvidia/x:free", free=True),
        ModelInfo(id="openai/gpt-4o-mini", free=False),
    ]
    index = main._engine_combo.findData("openrouter")
    main._engine_combo.setCurrentIndex(index)
    main._key_edit.setText("sk-or-fake")
    main._on_validate_clicked()
    import time

    for _ in range(60):
        qapp.processEvents()
        if main._model_combo.count() >= 2:
            break
        time.sleep(0.02)
    assert main._model_combo.count() == 2
    assert main._model_combo.itemData(0) == "nvidia/x:free"
    assert main._model_combo.isEnabled()
    # Elegir un modelo lo persiste en settings.
    main._model_combo.setCurrentIndex(1)
    assert main._settings.translation.model == "openai/gpt-4o-mini"
    for window in main.windows.values():
        window.hide()
