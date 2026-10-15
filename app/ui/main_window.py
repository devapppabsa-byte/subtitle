"""Panel de control principal de la aplicación.

Fase 6: ``MainWindow`` arranca/detiene el pipeline a través del
:class:`app.ui.bridge.PipelineBridge`, enruta cada cue a la ventana de su
idioma (``update_subtitle(language, text)``) y permite activar idiomas,
cambiar fuente y opacidad. Sin red en la UI: solo eventos y widgets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.ui.subtitle_window import ORIGINAL_KEY, SubtitleWindow, create_subtitle_window

if TYPE_CHECKING:  # pragma: no cover
    from app.core.pipeline import SubtitlePipeline
    from app.settings.config import AppSettings
    from app.subtitles.models import SubtitleCue
    from app.ui.bridge import PipelineBridge

__all__ = ["MainWindow"]


class MainWindow:
    """Panel de control + registro de ventanas por idioma."""

    def __init__(
        self,
        settings: "AppSettings",
        pipeline: "SubtitlePipeline",
        bridge: "PipelineBridge",
    ) -> None:
        from PySide6 import QtCore, QtWidgets

        self._settings = settings
        self._pipeline = pipeline
        self._bridge = bridge
        self._windows: dict[str, SubtitleWindow] = {}
        self._running = False

        window = QtWidgets.QMainWindow()
        window.setWindowTitle("Subtítulos en vivo")
        central = QtWidgets.QWidget(window)
        window.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        self._toggle = QtWidgets.QPushButton("Iniciar", central)
        self._toggle.clicked.connect(self.toggle_running)
        layout.addWidget(self._toggle)

        self._langs = QtWidgets.QGroupBox("Idiomas (ventanas)", central)
        langs_layout = QtWidgets.QVBoxLayout(self._langs)
        layout.addWidget(self._langs)
        self._lang_checks: dict[str, QtWidgets.QCheckBox] = {}
        self._langs_layout = langs_layout

        font_row = QtWidgets.QHBoxLayout()
        font_row.addWidget(QtWidgets.QLabel("Fuente:", central))
        self._font = QtWidgets.QSpinBox(central)
        self._font.setRange(10, 96)
        self._font.setValue(settings.overlay.style.font_size)
        self._font.valueChanged.connect(self._on_font_changed)
        font_row.addWidget(self._font)
        layout.addLayout(font_row)

        opacity_row = QtWidgets.QHBoxLayout()
        opacity_row.addWidget(QtWidgets.QLabel("Opacidad:", central))
        self._opacity = QtWidgets.QSlider(central)
        self._opacity.setOrientation(QtCore.Qt.Orientation.Horizontal)
        self._opacity.setRange(20, 100)
        self._opacity.setValue(100)
        self._opacity.valueChanged.connect(self._on_opacity_changed)
        opacity_row.addWidget(self._opacity)
        layout.addLayout(opacity_row)

        self._top = QtWidgets.QCheckBox("Siempre encima", central)
        self._top.setChecked(settings.overlay.always_on_top)
        self._top.toggled.connect(self._on_top_toggled)
        layout.addWidget(self._top)

        self._status = QtWidgets.QLabel("Detenido", central)
        layout.addWidget(self._status)

        prefs = QtWidgets.QPushButton("Ajustes…", central)
        prefs.clicked.connect(self.open_settings)
        layout.addWidget(prefs)

        self._window = window
        self._enabled: set[str] = set(self._active_languages())
        self._rebuild_language_checks()
        bridge.set_router(self._route_cue)

    # -- API de Fase 6 ------------------------------------------------------
    @property
    def widget(self):
        """QMainWindow subyacente."""
        return self._window

    @property
    def windows(self) -> dict[str, SubtitleWindow]:
        """Ventanas abiertas por idioma."""
        return dict(self._windows)

    def show(self) -> None:
        self._window.show()

    def toggle_running(self) -> None:
        """Inicia o detiene el pipeline (con el botón bloqueado en transición)."""
        from app.core.logging import get_logger

        self._toggle.setEnabled(False)
        try:
            if self._running:
                self._bridge.stop()
                self._running = False
                self._toggle.setText("Iniciar")
                self._status.setText("Detenido")
            else:
                self._bridge.start()
                self._running = True
                self._toggle.setText("Detener")
                self._status.setText("Escuchando…")
        except Exception:
            get_logger(__name__).exception("fallo al cambiar el estado del pipeline")
            self._status.setText("Error: ver el log")
        finally:
            self._toggle.setEnabled(True)

    def on_pipeline_finished(self, reason: str) -> None:
        """La fuente terminó: el panel SIGUE abierto para reiniciar.

        (Antes esto cerraba la app; por eso el lanzador “se abría y cerraba solo”
        con el demo sintético de 5 s.)
        """
        if self._running:
            try:
                self._bridge.stop()
            except Exception:
                pass
            self._running = False
        self._toggle.setText("Iniciar")
        self._status.setText(f"Finalizado ({reason}) — pulsa Iniciar")

    def update_subtitle(self, language: str, text: str, *, is_final: bool = True) -> None:
        """Actualiza el texto de la ventana del idioma (creándola si falta)."""
        key = (language or ORIGINAL_KEY).lower()
        window = self._windows.get(key)
        if window is None:
            window = create_subtitle_window(key, style=self._settings.overlay.style)
            window.toggle_always_on_top(self._settings.overlay.always_on_top)
            self._windows[key] = window
            self._rebuild_language_checks()
        window.update_subtitle(text, is_final=is_final)

    def open_settings(self) -> None:
        """Abre el diálogo de ajustes."""
        from app.ui.settings_dialog import SettingsDialog

        SettingsDialog(self._settings, on_apply=self._apply_settings).exec()

    # -- internos -----------------------------------------------------------
    def _route_cue(self, cue: "SubtitleCue") -> None:
        # Ventana original con el texto fuente + ventana exclusiva por destino.
        if cue.source_text and ORIGINAL_KEY in self._enabled:
            self.update_subtitle(ORIGINAL_KEY, cue.source_text, is_final=cue.is_final)
        key = (cue.target_language or ORIGINAL_KEY).lower()
        if key != ORIGINAL_KEY and key in self._enabled:
            self.update_subtitle(key, cue.text, is_final=cue.is_final)

    def _active_languages(self) -> list[str]:
        targets = self._pipeline.target_languages or [self._settings.translation.target_language]
        return [ORIGINAL_KEY, *[t.lower() for t in targets]]

    def _rebuild_language_checks(self) -> None:
        from PySide6 import QtWidgets

        for check in self._lang_checks.values():
            self._langs_layout.removeWidget(check)
            check.deleteLater()
        self._lang_checks = {}
        for lang in self._active_languages():
            check = QtWidgets.QCheckBox(lang, self._window)
            check.blockSignals(True)
            check.setChecked(lang in self._enabled)
            check.blockSignals(False)
            check.toggled.connect(lambda on, l=lang: self._on_language_toggled(l, on))
            self._langs_layout.addWidget(check)
            self._lang_checks[lang] = check

    def _on_language_toggled(self, language: str, enabled: bool) -> None:
        if enabled:
            self._enabled.add(language)
            previous = self._windows[language].last_text if language in self._windows else ""
            self.update_subtitle(language, previous)
        else:
            self._enabled.discard(language)
            window = self._windows.pop(language, None)
            if window is not None:
                window.hide()

    def _on_font_changed(self, size: int) -> None:
        import dataclasses

        self._settings.overlay.style = dataclasses.replace(
            self._settings.overlay.style, font_size=size
        )
        for window in self._windows.values():
            window.set_font_size(size)

    def _on_opacity_changed(self, value: int) -> None:
        for window in self._windows.values():
            window.set_window_opacity(value / 100.0)

    def _on_top_toggled(self, enabled: bool) -> None:
        self._settings.overlay.always_on_top = enabled
        for window in self._windows.values():
            window.toggle_always_on_top(enabled)

    def _apply_settings(self) -> None:
        self._font.blockSignals(True)
        self._font.setValue(self._settings.overlay.style.font_size)
        self._font.blockSignals(False)
        self._top.setChecked(self._settings.overlay.always_on_top)
        for window in list(self._windows.values()):
            window.set_font_size(self._settings.overlay.style.font_size)
            window.toggle_always_on_top(self._settings.overlay.always_on_top)
