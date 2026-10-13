"""Interfaz de usuario de la aplicación (panel de control).

Reservado para fases posteriores. Aquí vivirán el panel de control, la bandeja
del sistema, los atajos de teclado y la ventana de preferencias que edita
:class:`app.settings.config.AppSettings`.

En Fase 0 este paquete solo fija el límite arquitectónico: la UI consume
eventos del pipeline (``SubtitlePipeline.cues``) y dibuja a través de
``SubtitleRenderer``; nunca habla directamente con los motores de audio, voz o
traducción.
"""
