"""Ventana flotante de subtítulos para UN idioma.

Fase 6: sin bordes (``FramelessWindowHint``), siempre encima y translúcida.
Se arrastra con el ratón, se redimensiona con el grip de la esquina y puede
bloquearse (candado), ocultarse (×) o regularse en opacidad. La geometría
persiste por idioma en ``QSettings``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.errors import RenderError
from app.ui.styles import badge_stylesheet, button_stylesheet, subtitle_stylesheet

if TYPE_CHECKING:  # pragma: no cover
    from app.subtitles.models import SubtitleStyle

__all__ = ["SubtitleWindow", "create_subtitle_window", "ORIGINAL_KEY"]

#: Clave de la ventana del texto original (sin traducir).
ORIGINAL_KEY = "original"

_SETTINGS_ORG = "subtitle-app"


def _qt():  # pragma: no cover - importado en tests con offscreen
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError as exc:
        raise RenderError("SubtitleWindow requiere PySide6.") from exc
    return QtCore, QtGui, QtWidgets


class SubtitleWindow:
    """Ventana flotante para un idioma (envoltorio testeable sobre QWidget)."""

    def __init__(self, language: str, *, style=None, opacity: float = 1.0) -> None:
        QtCore, QtGui, QtWidgets = _qt()
        from app.subtitles.models import SubtitleStyle

        self._language = (language or ORIGINAL_KEY).lower()
        self._style: SubtitleStyle = style or SubtitleStyle()
        self._opacity = opacity
        self._font_size = self._style.font_size
        self._locked = False
        self._drag_offset = None
        self._last_text = ""

        window = QtWidgets.QWidget()
        window.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        window.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)

        badge = QtWidgets.QLabel(self._language.upper(), window)
        badge.setStyleSheet(badge_stylesheet(self._language))

        label = QtWidgets.QLabel("", window)
        label.setWordWrap(True)
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        label.setFont(QtGui.QFont(self._style.font_family, self._font_size))

        bar = QtWidgets.QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.addWidget(badge)
        bar.addStretch(1)
        lock = QtWidgets.QToolButton(window)
        lock.setText("🔒")
        lock.setCheckable(True)
        lock.setStyleSheet(button_stylesheet())
        lock.toggled.connect(self.set_locked)
        close = QtWidgets.QToolButton(window)
        close.setText("×")
        close.setStyleSheet(button_stylesheet())
        close.clicked.connect(window.hide)
        bar.addWidget(lock)
        bar.addWidget(close)

        grip = QtWidgets.QSizeGrip(window)

        layout = QtWidgets.QVBoxLayout(window)
        layout.addLayout(bar)
        layout.addWidget(label)
        bottom = QtWidgets.QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(grip)
        layout.addLayout(bottom)

        window.resize(560, 140)
        window.mousePressEvent = self._on_mouse_press
        window.mouseMoveEvent = self._on_mouse_move

        self._qt = (QtCore, QtGui, QtWidgets)
        self._window = window
        self._label = label
        self._lock_button = lock
        self._apply_style()

    # -- API de Fase 6 ------------------------------------------------------
    @property
    def language(self) -> str:
        """Idioma asignado a esta ventana (``original`` para el texto fuente)."""
        return self._language

    @property
    def widget(self):
        """QWidget subyacente (para anclar en layouts o tests)."""
        return self._window

    @property
    def last_text(self) -> str:
        """Último texto pintado (los parciales no borran el final previo)."""
        return self._last_text

    def show(self) -> None:
        self.restore_geometry()
        self._window.show()

    def hide(self) -> None:
        self.save_window_geometry()
        self._window.hide()

    def update_subtitle(self, text: str, *, is_final: bool = True) -> None:
        """Pinta el texto de ESTE idioma; los parciales se atenúan."""
        self._label.setStyleSheet(
            subtitle_stylesheet(self._style, font_size=self._font_size, dimmed=not is_final)
        )
        self._label.setText(text)
        if is_final and text:
            self._last_text = text

    def clear(self) -> None:
        self._label.setText("")
        self._last_text = ""

    def set_locked(self, locked: bool) -> None:
        """Bloquea/desbloquea el arrastre (botón 🔒)."""
        self._locked = bool(locked)
        if self._lock_button.isChecked() != self._locked:
            self._lock_button.setChecked(self._locked)

    def toggle_always_on_top(self, enabled: bool) -> None:
        """Controla si permanece encima de otras ventanas."""
        QtCore, _, _ = self._qt
        flags = self._window.windowFlags()
        if enabled:
            flags |= QtCore.Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~QtCore.Qt.WindowType.WindowStaysOnTopHint
        self._window.setWindowFlags(flags)
        if self._window.isVisible():
            self._window.show()

    def set_window_opacity(self, opacity: float) -> None:
        """Ajusta la transparencia (0.2–1.0)."""
        self._opacity = max(0.2, min(1.0, float(opacity)))
        self._window.setWindowOpacity(self._opacity)

    def set_font_size(self, size: int) -> None:
        """Cambia el tamaño de fuente de esta ventana."""
        _, QtGui, _ = self._qt
        self._font_size = max(10, min(96, int(size)))
        font = self._label.font()
        font.setPointSize(self._font_size)
        self._label.setFont(font)
        self._apply_style()

    def save_window_geometry(self) -> None:
        """Guarda posición y tamaño en QSettings (por idioma)."""
        _, _, QtWidgets = self._qt
        settings = QtWidgets.QApplication.instance() and _settings(self._language)
        if settings is None:
            return
        settings.setValue("geometry", self._window.saveGeometry())
        settings.sync()

    def restore_geometry(self) -> None:
        """Restaura posición y tamaño si existen."""
        _, _, QtWidgets = _qt()
        if QtWidgets.QApplication.instance() is None:
            return
        settings = _settings(self._language)
        geometry = settings.value("geometry")
        if geometry is not None:
            self._window.restoreGeometry(geometry)

    # -- internos -----------------------------------------------------------
    def _apply_style(self) -> None:
        self._label.setStyleSheet(subtitle_stylesheet(self._style, font_size=self._font_size))
        self.set_window_opacity(self._opacity)

    def _on_mouse_press(self, event) -> None:
        QtCore, _, _ = self._qt
        if event.button() == QtCore.Qt.MouseButton.LeftButton and not self._locked:
            self._drag_offset = event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()

    def _on_mouse_move(self, event) -> None:
        QtCore, _, _ = self._qt
        if (
            self._drag_offset is not None
            and event.buttons() & QtCore.Qt.MouseButton.LeftButton
            and not self._locked
        ):
            self._window.move(event.globalPosition().toPoint() - self._drag_offset)


def _settings(language: str):
    from PySide6 import QtCore

    return QtCore.QSettings(_SETTINGS_ORG, f"subtitle/{language}")


def create_subtitle_window(language: str, *, style=None, opacity: float = 1.0) -> SubtitleWindow:
    """Crea una ventana nueva para el idioma dado (especificación Fase 6)."""
    window = SubtitleWindow(language, style=style, opacity=opacity)
    window.show()
    return window
