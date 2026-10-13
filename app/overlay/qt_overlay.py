"""Overlay de subtítulos con Qt (ventana siempre encima).

Adaptador de ``SubtitleRenderer`` para escritorio. Qt se importa de forma
perezosa para que el núcleo de la aplicación funcione sin dependencias
gráficas instaladas (servidores, CI, tests).

Nota de integración: esta versión se bombea con ``processEvents`` desde el
bucle principal, que es donde el pipeline invoca ``render``. Fases posteriores
moverán la aplicación a un ``QApplication.exec()`` real con panel de control.
"""

from __future__ import annotations

from app.core.errors import RenderError
from app.subtitles.base import SubtitleRenderer
from app.subtitles.models import SubtitleCue, SubtitlePosition, SubtitleStyle

__all__ = ["QtOverlayRenderer"]


class QtOverlayRenderer(SubtitleRenderer):
    """Ventana sin bordes, translúcida y siempre encima con el último subtítulo."""

    def __init__(self, *, style: SubtitleStyle | None = None) -> None:
        super().__init__()
        self._style = style or SubtitleStyle()
        self._app = None
        self._window = None
        self._label = None

    def _on_start(self) -> None:
        try:
            from PySide6 import QtCore, QtGui, QtWidgets
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise RenderError(
                "El renderizador Qt requiere PySide6. Instalalo con 'pip install PySide6' "
                "o usá el renderizador 'console'."
            ) from exc

        self._app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

        window = QtWidgets.QWidget()
        window.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        window.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)

        label = QtWidgets.QLabel("", window)
        label.setWordWrap(True)
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        label.setFont(QtGui.QFont(self._style.font_family, self._style.font_size))
        label.setStyleSheet(
            f"color: {self._style.text_color};"
            f"background-color: rgba(0, 0, 0, {int(self._style.background_opacity * 255)});"
            "padding: 12px; border-radius: 8px;"
        )

        layout = QtWidgets.QVBoxLayout(window)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label)

        geometry = self._app.primaryScreen().availableGeometry()
        window.setFixedWidth(int(geometry.width() * 0.8))
        window.adjustSize()
        self._reposition(window, geometry)

        window.show()
        self._window, self._label = window, label

    def _on_stop(self, timeout: float | None) -> None:
        if self._window is not None:
            self._window.close()
        self._window = None
        self._label = None

    def render(self, cue: SubtitleCue) -> None:
        if self._window is None or self._label is None:
            raise RenderError("El overlay Qt no está iniciado")
        self._label.setText(cue.text)
        self._window.adjustSize()
        self._reposition(self._window, self._app.primaryScreen().availableGeometry())
        self._app.processEvents()

    def clear(self) -> None:
        if self._label is not None:
            self._label.setText("")
            self._app.processEvents()

    # -- helpers ----------------------------------------------------------
    def _reposition(self, window, geometry) -> None:
        margin = self._style.margin_pixels
        if self._style.position is SubtitlePosition.TOP:
            y = geometry.top() + margin
        elif self._style.position is SubtitlePosition.CENTER:
            y = geometry.top() + (geometry.height() - window.height()) // 2
        else:
            y = geometry.bottom() - window.height() - margin
        x = geometry.left() + (geometry.width() - window.width()) // 2
        window.move(x, y)
