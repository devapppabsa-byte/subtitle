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


def test_file_logging_writes(tmp_path):
    import logging

    from app.core.logging import configure_file_logging, configure_logging

    configure_logging(logging.INFO)
    target = configure_file_logging(str(tmp_path / "sub" / "app.log"))
    assert target.endswith("app.log")
    logging.getLogger("subtitle_app.test_probe").info("hola-log")
    assert "hola-log" in (tmp_path / "sub" / "app.log").read_text()
