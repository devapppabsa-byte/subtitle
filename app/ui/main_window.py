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
        *,
        on_engine_change=None,
    ) -> None:
        from PySide6 import QtCore, QtWidgets

        self._settings = settings
        self._pipeline = pipeline
        self._bridge = bridge
        self._on_engine_change = on_engine_change
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

        self._build_ai_section(central, layout)

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

    # -- sección "Motor IA" -------------------------------------------------
    def _build_ai_section(self, central, layout) -> None:
        """Combo de proveedor + clave + botón Validar (red en hilo aparte)."""
        from PySide6 import QtCore, QtWidgets

        from app.translation.providers import PROVIDERS

        self._engine_ids: list[str | None] = [None, *PROVIDERS.keys()]
        group = QtWidgets.QGroupBox("Motor IA (traducción)", central)
        form = QtWidgets.QFormLayout(group)
        layout.addWidget(group)
        self._ai_group = group

        self._engine_combo = QtWidgets.QComboBox(group)
        self._engine_combo.addItem("Sin IA (directo)", None)
        for provider in PROVIDERS.values():
            self._engine_combo.addItem(provider.label, provider.id)
        current = self._current_provider_id()
        index = self._engine_combo.findData(current)
        self._engine_combo.setCurrentIndex(index if index >= 0 else 0)
        self._engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        form.addRow("Proveedor:", self._engine_combo)

        from PySide6 import QtCore

        class _ValidationRelay(QtCore.QObject):
            result = QtCore.Signal(str, str, str, bool)

        self._relay = _ValidationRelay(group)
        self._relay.result.connect(self._finish_validation)

        self._key_edit = QtWidgets.QLineEdit(group)
        self._key_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self._key_edit.setPlaceholderText("Pega la API key del proveedor")
        form.addRow("API key:", self._key_edit)

        self._validate_btn = QtWidgets.QPushButton("Validar y usar", group)
        self._validate_btn.clicked.connect(self._on_validate_clicked)
        form.addRow("", self._validate_btn)

        self._ai_status = QtWidgets.QLabel("", group)
        self._ai_status.setWordWrap(True)
        form.addRow("", self._ai_status)

        self._validator = None  # inyectable en tests
        self._refresh_ai_section()

    def _current_provider_id(self) -> str | None:
        from app.translation.providers import provider_for_engine

        provider = provider_for_engine(self._settings.translation.engine)
        return provider.id if provider else None

    def _refresh_ai_section(self) -> None:
        """Ajusta pistas/estado según el proveedor seleccionado."""
        import os

        from PySide6 import QtWidgets

        from app.translation.providers import provider_by_id

        provider_id = self._engine_combo.currentData()
        known = os.environ.get(provider_by_id(provider_id).key_env) if provider_id else ""
        self._key_edit.setEnabled(provider_id is not None)
        self._validate_btn.setEnabled(provider_id is not None)
        if provider_id is None:
            self._ai_status.setText("Traducción directa: se muestra el texto original.")
            return
        self._key_edit.setPlaceholderText(
            "Clave guardada — pega otra para cambiarla" if known else "Pega la API key del proveedor"
        )
        self._ai_status.setText(
            f"{provider_by_id(provider_id).label}: clave {'detectada' if known else 'no detectada'}."
        )

    def _on_engine_changed(self, _index: int) -> None:
        self._refresh_ai_section()

    def _on_validate_clicked(self) -> None:
        """Valida (sin bloquear la GUI) y aplica el proveedor si es válido."""
        import threading

        from PySide6 import QtWidgets

        from app.translation.providers import provider_by_id

        provider_id = self._engine_combo.currentData()
        if provider_id is None:
            self._apply_no_ai_engine()
            return
        provider = provider_by_id(provider_id)
        api_key = self._key_edit.text().strip()
        if not api_key:
            import os

            api_key = os.environ.get(provider.key_env, "")
        if not api_key:
            self._ai_status.setText(f"Falta la clave de {provider.label} ({provider.key_env}).")
            return

        self._validate_btn.setEnabled(False)
        self._ai_status.setText(f"Verificando {provider.label}…")

        def _work() -> None:
            from app.settings.store import validate_key

            validator = self._validator
            if validator is not None:
                report = validator(provider.id, api_key)
            else:
                report = validate_key(provider.id, api_key, key_env=provider.key_env)
            self._relay.result.emit(provider.id, api_key, report.detail, report.ok)

        threading.Thread(target=_work, name="ai-validate", daemon=True).start()

    def _finish_validation(self, provider_id: str, api_key: str, detail: str, ok: bool) -> None:
        """Slot en el hilo GUI: aplica el proveedor o informa del fallo."""
        from app.core.logging import get_logger

        from app.translation.providers import provider_by_id

        self._validate_btn.setEnabled(True)
        provider = provider_by_id(provider_id)
        if not ok:
            self._ai_status.setText(f"{provider.label}: {detail or 'clave no válida'}")
            return
        self._ai_status.setText(f"{provider.label}: ✓ {detail}")
        self._activate_provider(provider, api_key, detail)

    def _activate_provider(self, provider, api_key: str, detail: str) -> None:
        """Persiste la clave, la inyecta en el entorno y cambia el motor en vivo."""
        import os

        from app.core.logging import get_logger
        from app.settings.store import save_env_key

        logger = get_logger(__name__)
        os.environ[provider.key_env] = api_key
        self._settings.translation.engine = provider.id
        try:
            save_env_key(provider.key_env, api_key)
        except OSError:
            logger.exception("no se pudo guardar la clave en el .env del usuario")
        self._swap_translator(provider.id, api_key)

    def _apply_no_ai_engine(self) -> None:
        """Modo sin IA: traducción directa (passthrough)."""
        from app.core.factory import build_translator

        self._settings.translation.engine = "passthrough"
        self._swap_translator_from_factory()

    def _swap_translator(self, provider_id: str, api_key: str) -> None:
        from app.core.factory import build_translation_engine

        try:
            translator = build_translation_engine(
                provider_id, api_key=api_key, translation=self._settings.translation
            )
            if self._on_engine_change is not None:
                self._on_engine_change(translator)
            else:
                self._pipeline.swap_translator(translator)
        except Exception as exc:
            from app.core.logging import get_logger

            get_logger(__name__).exception("no se pudo activar el proveedor %s", provider_id)
            self._ai_status.setText(f"No se pudo activar: {exc}")

    def _swap_translator_from_factory(self) -> None:
        from app.core.factory import build_translator

        try:
            translator = build_translator(self._settings)
            if self._on_engine_change is not None:
                self._on_engine_change(translator)
            else:
                self._pipeline.swap_translator(translator)
        except Exception as exc:
            self._ai_status.setText(f"No se pudo activar: {exc}")

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
