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

        self._build_audio_section(central, layout)
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

    # -- sección "Origen de audio" -----------------------------------------
    def _build_audio_section(self, central, layout) -> None:
        """Selector de dónde sale el audio (todo el sistema, app o pestaña…)."""
        from PySide6 import QtCore, QtWidgets

        group = QtWidgets.QGroupBox("Origen de audio", central)
        form = QtWidgets.QFormLayout(group)
        layout.addWidget(group)
        self._audio_group = group

        class _StreamsRelay(QtCore.QObject):
            loaded = QtCore.Signal(object, str)

        self._streams_relay = _StreamsRelay(group)
        self._streams_relay.loaded.connect(self._finish_streams)

        self._origin_combo = QtWidgets.QComboBox(group)
        for label, value in (
            ("Todo el sistema (monitor)", "monitor"),
            ("Aplicación o pestaña", "app"),
            ("Micrófono", "microphone"),
            ("Demo (sintético)", "synthetic"),
            ("Archivo WAV", "wav"),
        ):
            self._origin_combo.addItem(label, value)
        index = self._origin_combo.findData(self._settings.audio.source)
        self._origin_combo.setCurrentIndex(index if index >= 0 else 0)
        self._origin_combo.currentIndexChanged.connect(self._on_origin_changed)
        form.addRow("Origen:", self._origin_combo)

        self._stream_combo = QtWidgets.QComboBox(group)
        self._stream_combo.setEnabled(False)
        form.addRow("App / pestaña:", self._stream_combo)

        self._refresh_streams_btn = QtWidgets.QPushButton("Actualizar", group)
        self._refresh_streams_btn.clicked.connect(self._on_refresh_streams)
        form.addRow("", self._refresh_streams_btn)

        self._loopback_check = QtWidgets.QCheckBox("Seguir oyendo por los parlantes", group)
        self._loopback_check.setChecked(self._settings.audio.stream_loopback)
        form.addRow("", self._loopback_check)

        self._apply_origin_btn = QtWidgets.QPushButton("Aplicar origen", group)
        self._apply_origin_btn.clicked.connect(self._on_apply_origin)
        form.addRow("", self._apply_origin_btn)

        self._audio_status = QtWidgets.QLabel("", group)
        self._audio_status.setWordWrap(True)
        form.addRow("", self._audio_status)

        self._streams_fetcher = None  # inyectable en tests
        self._on_origin_changed(self._origin_combo.currentIndex())

    def _on_origin_changed(self, index: int) -> None:
        """Habilita el selector de stream solo para el origen 'app'."""
        is_app = self._origin_combo.itemData(index) == "app"
        self._stream_combo.setEnabled(is_app)
        self._refresh_streams_btn.setEnabled(is_app)
        if is_app and self._stream_combo.count() == 0:
            self._on_refresh_streams()

    def _on_refresh_streams(self) -> None:
        """Lista los streams activos (apps/pestañas) sin bloquear la interfaz."""
        import threading

        self._refresh_streams_btn.setEnabled(False)
        self._audio_status.setText("Buscando aplicaciones y pestañas con audio…")

        def _work() -> None:
            fetcher = self._streams_fetcher
            try:
                if fetcher is not None:
                    streams = fetcher()
                    error = ""
                else:
                    from app.audio.streams import list_audio_streams

                    streams = list_audio_streams()
                    error = ""
            except Exception as exc:  # pragma: no cover - defensivo
                streams, error = [], str(exc)
            self._streams_relay.loaded.emit(list(streams), error)

        threading.Thread(target=_work, name="audio-streams", daemon=True).start()

    def _finish_streams(self, streams, error: str) -> None:
        """Rellena el combo de streams con lo detectado."""
        self._refresh_streams_btn.setEnabled(True)
        if error:
            self._audio_status.setText(f"No se pudieron detectar los streams: {error}")
            return
        current = self._stream_combo.currentData()
        self._stream_combo.blockSignals(True)
        self._stream_combo.clear()
        for stream in streams:
            self._stream_combo.addItem(stream.label, stream.spec)
        self._stream_combo.blockSignals(False)
        if not streams:
            self._audio_status.setText(
                "No hay ninguna app o pestaña reproduciendo audio. "
                "Reproducí algo y volvé a pulsar Actualizar."
            )
            return
        if current:
            index = self._stream_combo.findData(current)
            if index >= 0:
                self._stream_combo.setCurrentIndex(index)
        self._audio_status.setText(f"{len(streams)} streams detectados.")

    def _on_apply_origin(self) -> None:
        """Aplica el origen elegido: reconstruye la fuente y reinicia si corre."""
        from app.core.factory import build_audio_source
        from app.core.logging import get_logger

        logger = get_logger(__name__)
        source_id = str(self._origin_combo.currentData() or "monitor")
        stream_spec: str | None = None
        if source_id == "app":
            stream_spec = self._stream_combo.currentData()
            if not stream_spec:
                self._audio_status.setText(
                    "Elegí una aplicación o pestaña (pulsá Actualizar para detectarla)."
                )
                return
        # La configuración solo se toca cuando el origen es válido: si la
        # construcción de la fuente falla, el estado anterior queda intacto.
        previous = (
            self._settings.audio.source,
            self._settings.audio.stream_spec,
            self._settings.audio.stream_loopback,
        )
        self._settings.audio.source = source_id
        self._settings.audio.stream_loopback = self._loopback_check.isChecked()
        if stream_spec is not None:
            self._settings.audio.stream_spec = str(stream_spec)
        try:
            source = build_audio_source(self._settings)
        except Exception as exc:
            (
                self._settings.audio.source,
                self._settings.audio.stream_spec,
                self._settings.audio.stream_loopback,
            ) = previous
            self._audio_status.setText(f"No se pudo preparar el origen: {exc}")
            logger.exception("origen de audio inválido")
            return
        try:
            self._swap_audio_source(source)
        except Exception as exc:
            self._audio_status.setText(f"No se pudo aplicar el origen: {exc}")
            logger.exception("fallo al cambiar la fuente de audio")
            return
        label = self._origin_combo.currentText()
        if source_id == "app":
            label = f"{label} ({self._stream_combo.currentText()})"
        self._audio_status.setText(f"Origen aplicado: {label}.")

    def _swap_audio_source(self, source) -> None:
        """Cambia la fuente en vivo: stop → set_source → start."""
        was_running = self._running
        if was_running:
            self._bridge.stop()
            self._running = False
        self._pipeline.set_source(source)
        if was_running:
            self._bridge.start()
            self._running = True
            self._toggle.setText("Detener")
            self._status.setText("Escuchando…")

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
            label = f"{provider.label} · gratis" if provider.free_tier else provider.label
            self._engine_combo.addItem(label, provider.id)
        current = self._current_provider_id()
        index = self._engine_combo.findData(current)
        self._engine_combo.setCurrentIndex(index if index >= 0 else 0)
        self._engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        form.addRow("Proveedor:", self._engine_combo)

        from PySide6 import QtCore

        class _Relay(QtCore.QObject):
            result = QtCore.Signal(str, str, str, bool)

        class _ModelsRelay(QtCore.QObject):
            loaded = QtCore.Signal(str, object, str)

        self._relay = _Relay(group)
        self._relay.result.connect(self._finish_validation)
        self._models_relay = _ModelsRelay(group)
        self._models_relay.loaded.connect(self._finish_models)

        self._key_edit = QtWidgets.QLineEdit(group)
        self._key_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self._key_edit.setPlaceholderText("Pega la API key del proveedor")
        form.addRow("API key:", self._key_edit)

        self._validate_btn = QtWidgets.QPushButton("Validar y usar", group)
        self._validate_btn.clicked.connect(self._on_validate_clicked)
        form.addRow("", self._validate_btn)

        self._model_combo = QtWidgets.QComboBox(group)
        self._model_combo.setEditable(True)  # permite pegar un id no listado
        self._model_combo.setEnabled(False)
        self._model_combo.currentIndexChanged.connect(self._on_model_changed)
        form.addRow("Modelo:", self._model_combo)

        self._ai_status = QtWidgets.QLabel("", group)
        self._ai_status.setWordWrap(True)
        form.addRow("", self._ai_status)

        self._validator = None  # inyectable en tests
        self._models_fetcher = None  # inyectable en tests
        self._refresh_ai_section()

    def _current_provider_id(self) -> str | None:
        from app.translation.providers import provider_for_engine

        provider = provider_for_engine(self._settings.translation.engine)
        return provider.id if provider else None

    def _refresh_ai_section(self) -> None:
        """Ajusta pistas/estado según el proveedor seleccionado."""
        import os

        from app.translation.providers import provider_by_id

        provider_id = self._engine_combo.currentData()
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        self._model_combo.blockSignals(False)
        self._model_combo.setEnabled(False)
        self._key_edit.setEnabled(provider_id is not None)
        self._validate_btn.setEnabled(provider_id is not None)
        if provider_id is None:
            self._ai_status.setText("Traducción directa: se muestra el texto original.")
            return
        provider = provider_by_id(provider_id)
        known = os.environ.get(provider.key_env, "")
        self._key_edit.setPlaceholderText(
            "Clave guardada — pega otra para cambiarla" if known else "Pega la API key del proveedor"
        )
        hint = f"{provider.label}: clave {'detectada' if known else 'no detectada'}."
        if provider.docs_url:
            hint += f"  ({provider.docs_url})"
        self._ai_status.setText(hint)

    def _on_engine_changed(self, _index: int) -> None:
        self._refresh_ai_section()
        self._maybe_load_models()

    def _current_key_for(self, provider) -> str:
        import os

        return self._key_edit.text().strip() or os.environ.get(provider.key_env, "")

    def _maybe_load_models(self) -> None:
        """Si ya hay clave para el proveedor, rellena el combo de modelos."""
        from app.translation.providers import provider_by_id

        provider_id = self._engine_combo.currentData()
        if not provider_id:
            return
        provider = provider_by_id(provider_id)
        api_key = self._current_key_for(provider)
        if api_key:
            self._load_models(provider_id, api_key)

    def _load_models(self, provider_id: str, api_key: str) -> None:
        import threading

        from app.core.logging import get_logger

        self._ai_status.setText("Cargando modelos…")

        def _work() -> None:
            from app.settings.store import fetch_models

            try:
                fetcher = self._models_fetcher
                models = fetcher(provider_id, api_key) if fetcher else fetch_models(provider_id, api_key)
                self._models_relay.loaded.emit(provider_id, models, "")
            except Exception as exc:
                self._models_relay.loaded.emit(provider_id, [], str(exc)[:160])
            finally:
                get_logger(__name__).debug("listado de modelos de %s terminado", provider_id)

        threading.Thread(target=_work, name="ai-models", daemon=True).start()

    def _finish_models(self, provider_id: str, models: object, error: str) -> None:
        """Slot GUI: rellena el combo de modelos del proveedor."""
        from app.translation.providers import provider_by_id

        if provider_id != self._engine_combo.currentData():
            return  # el usuario cambió de proveedor mientras se cargaba
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        items = list(models) if models else []
        for info in items:
            model_id = getattr(info, "id", str(info))
            free = getattr(info, "free", False)
            suffix = "  · gratis" if free else ""
            self._model_combo.addItem(f"{model_id}{suffix}", model_id)
        if not items:
            self._model_combo.setEnabled(False)
            self._model_combo.blockSignals(False)
            provider = provider_by_id(provider_id)
            if error:
                self._ai_status.setText(f"{provider.label}: no se pudieron cargar modelos ({error})")
            return
        # Selecciona el modelo configurado si está en la lista.
        wanted = (self._settings.translation.model or "").strip()
        idx = self._model_combo.findData(wanted)
        if idx >= 0:
            self._model_combo.setCurrentIndex(idx)
        self._model_combo.setEnabled(True)
        self._model_combo.blockSignals(False)
        provider = provider_by_id(provider_id)
        free_count = sum(1 for info in items if getattr(info, "free", False))
        extra = f" ({free_count} gratis)" if free_count else ""
        self._ai_status.setText(f"{provider.label}: {len(items)} modelos disponibles{extra}.")

    def _on_model_changed(self, _index: int) -> None:
        model_id = self._model_combo.currentData()
        if model_id:
            self._settings.translation.model = str(model_id)

    def _on_validate_clicked(self) -> None:
        """Valida (sin bloquear la GUI) y aplica el proveedor si es válido."""
        import threading

        from app.translation.providers import provider_by_id

        provider_id = self._engine_combo.currentData()
        if provider_id is None:
            self._apply_no_ai_engine()
            return
        provider = provider_by_id(provider_id)
        api_key = self._current_key_for(provider)
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
        from app.translation.providers import provider_by_id

        self._validate_btn.setEnabled(True)
        provider = provider_by_id(provider_id)
        if not ok:
            self._ai_status.setText(f"{provider.label}: {detail or 'clave no válida'}")
            return
        self._ai_status.setText(f"{provider.label}: ✓ {detail}")
        self._activate_provider(provider, api_key, detail)
        self._load_models(provider_id, api_key)  # rellena el combo con TODOS los modelos

    def _activate_provider(self, provider, api_key: str, detail: str) -> None:
        """Persiste la clave cifrada, la inyecta y cambia el motor en vivo."""
        import os

        from app.core.logging import get_logger
        from app.settings.keyvault import default_vault

        logger = get_logger(__name__)
        os.environ[provider.key_env] = api_key
        self._settings.translation.engine = provider.id
        try:
            persisted = default_vault().save_key(provider.id, provider.key_env, api_key)
            if not persisted:
                self._ai_status.setText(
                    f"{provider.label}: clave activa en esta sesión (llavero no disponible, no se guardó)."
                )
        except Exception:
            logger.exception("no se pudo guardar la clave cifrada de %s", provider.id)
        selected = self._model_combo.currentData() or self._settings.translation.model
        if selected:
            self._settings.translation.model = str(selected)
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
                provider_id,
                api_key=api_key,
                model=self._settings.translation.model,
                translation=self._settings.translation,
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

        Idempotente: si ya está detenido, no toca nada. El bridge ya paró su
        bombeo antes de emitir, así que aquí no se vuelve a llamar ``stop()``
        (hacerlo re-arrancaba por señales ``finished`` repetidas).
        """
        from app.core.logging import get_logger

        if not self._running:
            get_logger(__name__).debug("finished ignorado (ya detenido): %s", reason)
            return
        self._running = False
        self._toggle.setText("Iniciar")
        self._status.setText(f"Finalizado ({reason}) — pulsa Iniciar")
        get_logger(__name__).info("pipeline finalizado: %s", reason)

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
