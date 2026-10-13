"""Diálogo de ajustes de la aplicación.

Fase 6: edita el ``AppSettings`` vivo (idiomas destino, fuente, opacidad,
siempre-encima) con aplicación inmediata a las ventanas abiertas.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:  # pragma: no cover
    from app.settings.config import AppSettings

__all__ = ["SettingsDialog"]


class SettingsDialog:
    """QDialog para preferencias (envoltorio testeable)."""

    def __init__(
        self,
        settings: "AppSettings",
        *,
        on_apply: Callable[[], None] | None = None,
    ) -> None:
        from PySide6 import QtCore, QtWidgets

        self._settings = settings
        self._on_apply = on_apply

        dialog = QtWidgets.QDialog()
        dialog.setWindowTitle("Ajustes de subtítulos")
        layout = QtWidgets.QFormLayout(dialog)

        langs = QtWidgets.QLineEdit(dialog)
        langs.setText(", ".join(settings.translation.target_languages or [settings.translation.target_language]))
        langs.setPlaceholderText("es, fr, de")
        layout.addRow("Idiomas destino:", langs)

        font = QtWidgets.QSpinBox(dialog)
        font.setRange(10, 96)
        font.setValue(settings.overlay.style.font_size)
        layout.addRow("Tamaño de fuente:", font)

        opacity = QtWidgets.QSlider(dialog)
        opacity.setOrientation(QtCore.Qt.Orientation.Horizontal)
        opacity.setRange(20, 100)
        opacity.setValue(int(settings.overlay.style.background_opacity * 100))
        layout.addRow("Opacidad fondo:", opacity)

        on_top = QtWidgets.QCheckBox("Siempre encima", dialog)
        on_top.setChecked(settings.overlay.always_on_top)
        layout.addRow(on_top)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)

        dialog.accepted.connect(lambda: self._apply(langs.text(), font.value(), opacity.value(), on_top.isChecked()))

        save_default = QtWidgets.QPushButton("Guardar como predeterminado", dialog)
        save_default.setToolTip("Guarda en ~/.config/traductor-tiempo-real/settings.json")
        save_default.clicked.connect(lambda: self._apply_and_save(
            langs.text(), font.value(), opacity.value(), on_top.isChecked()))
        layout.addRow(save_default)

        self._dialog = dialog
        self._fields = (langs, font, opacity, on_top)

    @property
    def widget(self):
        """QDialog subyacente."""
        return self._dialog

    def exec(self) -> int:
        """Muestra el diálogo de forma modal."""
        return self._dialog.exec()

    # -- internos -----------------------------------------------------------
    def _apply(self, langs: str, font_size: int, opacity: int, on_top: bool) -> None:
        import dataclasses

        targets = [part.strip().lower() for part in langs.split(",") if part.strip()]
        if targets:
            self._settings.translation.target_languages = targets
            self._settings.translation.target_language = targets[0]
        self._settings.overlay.style = dataclasses.replace(
            self._settings.overlay.style,
            font_size=font_size,
            background_opacity=opacity / 100.0,
        )
        self._settings.overlay.always_on_top = on_top
        if self._on_apply is not None:
            self._on_apply()

    def _apply_and_save(self, langs: str, font_size: int, opacity: int, on_top: bool) -> None:
        """Aplica y persiste como base de futuros arranques."""
        from app.settings.store import save_settings

        self._apply(langs, font_size, opacity, on_top)
        save_settings(self._settings)
