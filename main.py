"""Punto de entrada de la aplicación de subtítulos.

Solo compone: lee configuración, construye las implementaciones concretas a
través del *composition root* y ejecuta el bucle principal. No contiene lógica
de dominio.
"""

from __future__ import annotations

import argparse
import sys

from app.core.factory import build_pipeline, build_renderer
from app.core.logging import configure_file_logging, configure_logging, get_logger
from app.settings.config import AppSettings
from app.settings.store import export_logs, load_user_settings, save_settings, validate_api_config

logger = get_logger("main")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="subtitle-app",
        description="Subtítulos en tiempo real con traducción.",
    )
    parser.add_argument("--config", help="Ruta a un archivo JSON de configuración.")
    parser.add_argument("--duration", type=float, default=None, help="Segundos de ejecución (por defecto: hasta que termine el audio).")
    parser.add_argument("--audio", choices=["synthetic", "wav", "microphone", "monitor"], help="Origen de audio.")
    parser.add_argument("--audio-path", help="Ruta del WAV cuando --audio=wav.")
    parser.add_argument("--audio-device", help="Índice o parte del nombre (None = auto).")
    parser.add_argument("--list-devices", action="store_true", help="Lista dispositivos y monitores, luego sale.")
    parser.add_argument(
        "--realtime",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Reproducir el audio al ritmo real (útil para ver los subtítulos en vivo).",
    )
    parser.add_argument("--speech-engine", help="Motor de reconocimiento de voz (scripted, groq).")
    parser.add_argument("--speech-model", help="Modelo de voz (groq: whisper-large-v3-turbo).")
    parser.add_argument("--speech-language", help="Idioma ISO o 'auto' para autodetectar.")
    parser.add_argument("--translation-engine", help="Motor de traducción (passthrough, mapping, groq).")
    parser.add_argument("--translation-model", help="Modelo de traducción (groq: openai/gpt-oss-20b).")
    parser.add_argument("--source-language", help="Idioma de origen ('auto' para detectar).")
    parser.add_argument("--target-language", help="Idioma de destino (uno solo).")
    parser.add_argument(
        "--target-languages",
        help="Varios destinos separados por coma del mismo fragmento (p. ej. es,fr,de).",
    )
    parser.add_argument("--renderer", choices=["console", "qt"], help="Dónde mostrar los subtítulos.")
    parser.add_argument("--speech-window", type=float, help="Ventana STT en segundos (groq).")
    parser.add_argument("--speech-overlap", type=float, help="Solape entre ventanas STT (groq).")
    parser.add_argument("--translation-parallel", action="store_true", help="Traducir destinos en paralelo.")
    parser.add_argument("--translation-cache", type=int, help="Tamaño de la caché LRU de traducciones.")
    parser.add_argument("--latency-log", help="Ruta JSONL para el log de latencia por tramos.")
    parser.add_argument("--save-config", action="store_true", help="Guardar prefs en ~/.config/traductor-tiempo-real/ al salir.")
    parser.add_argument("--validate-config", action="store_true", help="Verificar proveedores y salir (sin secretos).")
    parser.add_argument("--export-logs", metavar="DIR", help="Exportar diagnóstico redactado y salir.")
    parser.add_argument("--log-level", help="DEBUG, INFO, WARNING, ERROR.")
    return parser


def apply_cli_overrides(settings: AppSettings, args: argparse.Namespace) -> None:
    """Vuelca los argumentos presentes sobre la configuración cargada."""
    if args.audio is not None:
        settings.audio.source = args.audio
    if args.audio_path is not None:
        settings.audio.path = args.audio_path
    if args.audio_device is not None:
        settings.audio.device = args.audio_device
    if args.realtime is not None:
        settings.audio.realtime = args.realtime
    if args.speech_engine is not None:
        settings.speech.engine = args.speech_engine
    if args.speech_model is not None:
        settings.speech.model = args.speech_model
    if args.speech_language is not None:
        settings.speech.language = None if args.speech_language == "auto" else args.speech_language
    if args.translation_engine is not None:
        settings.translation.engine = args.translation_engine
    if args.translation_model is not None:
        settings.translation.model = args.translation_model
    if args.source_language is not None:
        settings.translation.source_language = args.source_language
    if args.target_language is not None:
        settings.translation.target_language = args.target_language
    if args.target_languages is not None:
        settings.translation.target_languages = [
            part.strip() for part in args.target_languages.split(",") if part.strip()
        ]
    if args.renderer is not None:
        settings.overlay.renderer = args.renderer
    if args.speech_window is not None:
        settings.speech.window_seconds = args.speech_window
    if args.speech_overlap is not None:
        settings.speech.window_overlap_seconds = args.speech_overlap
    if args.translation_parallel:
        settings.translation.parallel = True
    if args.translation_cache is not None:
        settings.translation.cache_size = args.translation_cache
    if args.latency_log is not None:
        settings.latency.latency_log = args.latency_log
    if args.log_level is not None:
        settings.log_level = args.log_level


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_devices:
        from app.audio.capture import find_monitor_devices, list_audio_devices

        devices = list_audio_devices()
        print(f"{'IDX':>3}  {'KIND':<10} IN/OUT  RATE      FLAGS  NAME")
        for dev in devices:
            flags = "".join(
                [
                    "M" if dev.is_monitor else "-",
                    "D" if dev.is_default else "-",
                ]
            )
            print(
                f"{dev.index:>3}  {dev.kind:<10} {dev.channels_in:>2}/{dev.channels_out:<2}  "
                f"{dev.default_rate:>7.0f}Hz  {flags}     {dev.name}"
                + (f"  [{dev.pactl_name}]" if dev.pactl_name else "")
            )
        monitors = find_monitor_devices(devices)
        print(f"\nMonitores del sistema: {len(monitors)}")
        for mon in monitors:
            print(f"  #{mon.index} {mon.name}")
        return 0
    settings = load_user_settings(args.config)
    apply_cli_overrides(settings, args)
    configure_logging(settings.log_level)

    if args.validate_config:
        for report in validate_api_config(settings):
            state = "OK" if report.ok else ("FALTA CLAVE" if not report.key_present else "ERROR")
            print(f"[{state}] {report.provider} ({report.key_env}): {report.detail}")
            for model in report.models:
                print(f"  - {model}")
        return 0
    if args.export_logs:
        export_logs(settings, None, args.export_logs, latency_log=settings.latency.latency_log or None)
        print(f"diagnóstico en {args.export_logs} (redactado, sin claves ni audio)")
        return 0

    logger.info(
        "configuración: audio=%s voz=%s traducción=%s render=%s (%s→%s)",
        settings.audio.source,
        settings.speech.engine,
        settings.translation.engine,
        settings.overlay.renderer,
        settings.translation.source_language,
        ",".join(settings.translation.target_languages)
        or settings.translation.target_language,
    )

    pipeline = build_pipeline(settings)
    renderer = build_renderer(settings)
    if settings.overlay.renderer == "qt":
        code = _run_qt(pipeline, settings, timeout=args.duration)
    else:
        try:
            with renderer:
                pipeline.run(renderer, timeout=args.duration)
        except KeyboardInterrupt:
            logger.info("interrumpido por el usuario")
        code = 0
    if args.save_config:
        save_settings(settings)
    return code


def _run_qt(pipeline, settings, timeout: float | None) -> int:
    """Panel principal + ventanas por idioma (Fase 6), sin red en la UI."""
    from PySide6.QtCore import QLockFile
    from PySide6.QtWidgets import QApplication

    from app.ui.bridge import PipelineBridge
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("subtitle-app")
    app.setApplicationName("subtitle-app")
    lock = QLockFile("/tmp/subtitle-app.lock")
    if not lock.tryLock(100):
        logger.error("ya hay una instancia corriendo (lock /tmp/subtitle-app.lock)")
        print("Ya hay una instancia corriendo; ciérrala antes de abrir otra.")
        return 2
    bridge = PipelineBridge(pipeline, None, poll_ms=settings.latency.poll_ms)
    window = MainWindow(settings, pipeline, bridge)
    window.show()
    configure_file_logging(str(_default_log_path()))
    window.toggle_running()  # arranca capturando sin pulsar Iniciar
    # En modo gráfico la ventana manda: el fin de una fuente finita NO cierra
    # la app (antes el lanzador parpadeaba y se cerraba con el demo de 5 s).
    bridge.finished.connect(window.on_pipeline_finished)
    if timeout is not None:
        from PySide6.QtCore import QTimer

        QTimer.singleShot(int(timeout * 1000), app.quit)
    try:
        app.exec()
    except KeyboardInterrupt:
        logger.info("interrumpido por el usuario")
    finally:
        try:
            bridge.stop()
        except Exception:
            logger.exception("fallo al detener el bridge Qt")
        for child in window.windows.values():
            try:
                child.save_window_geometry()
            except Exception:
                pass
    return 0


def _default_log_path():
    """Log persistente para lanzamientos sin terminal (menú de Omarchy)."""
    from pathlib import Path

    from app.settings.store import user_config_dir

    try:
        return Path(user_config_dir()) / "app.log"
    except OSError:
        return Path("/tmp/subtitle-app.log")


if __name__ == "__main__":
    sys.exit(main())
