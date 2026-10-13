"""Interfaz de usuario de la aplicación (panel de control).

Reservado para fases posteriores. Aquí vivirán el panel de control, la bandeja
del sistema, los atajos de teclado y la ventana de preferencias que edita
:class:`app.settings.config.AppSettings`.

En Fase 0 este paquete solo fija el límite arquitectónico: la UI consume
eventos del pipeline (``SubtitlePipeline.cues``) y dibuja a través de
``SubtitleRenderer``; nunca habla directamente con los motores de audio, voz o
traducción.

En Fase 5 el :class:`app.ui.bridge.PipelineBridge` drena la cola con un
``QTimer`` en el hilo GUI y reemite señales; las ventanas se actualizan en
slots, sin red en la UI.
"""

from app.ui.bridge import PipelineBridge
from app.ui.main_window import MainWindow
from app.ui.settings_dialog import SettingsDialog
from app.ui.styles import badge_stylesheet, button_stylesheet, subtitle_stylesheet
from app.ui.subtitle_window import ORIGINAL_KEY, SubtitleWindow, create_subtitle_window

__all__ = [
    "PipelineBridge",
    "MainWindow",
    "SettingsDialog",
    "SubtitleWindow",
    "ORIGINAL_KEY",
    "create_subtitle_window",
    "subtitle_stylesheet",
    "badge_stylesheet",
    "button_stylesheet",
]
