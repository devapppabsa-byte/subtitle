"""Persistencia de preferencias y diagnóstico (Fase 8).

Extiende ``app/settings/config.py`` sin duplicarlo (ver nota de Fase 8: no se
crea ``app/core/config.py``):

* ``user_config_dir`` → ``~/.config/traductor-tiempo-real/`` (respeta XDG).
* ``save_settings`` → vuelca ``to_dict()`` a ``settings.json`` (nunca claves).
* ``validate_api_config`` → comprueba la clave efectiva contra la cuenta y
  lista modelos, sin imprimir ni guardar el secreto.
* ``export_logs`` → empaqueta diagnóstico redactado (sin claves ni audio).
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core.errors import ConfigurationError
from app.core.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from app.settings.config import AppSettings

__all__ = [
    "APP_DIR_NAME",
    "SETTINGS_FILE",
    "ENV_FILE",
    "ValidationReport",
    "user_config_dir",
    "settings_path",
    "env_file_path",
    "save_settings",
    "save_env_key",
    "load_env_file",
    "load_user_settings",
    "validate_key",
    "validate_api_config",
    "export_logs",
]

logger = get_logger(__name__)

APP_DIR_NAME = "traductor-tiempo-real"
SETTINGS_FILE = "settings.json"
ENV_FILE = ".env"

_SECRET_PATTERNS = (
    re.compile(r"\bgsk_[A-Za-z0-9_-]+"),
    re.compile(r"\bsk-(?:ant-|or-)?[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)(['\"]?)[^'\"\s;]+(\2)"),
)


def redact_secrets(text: str) -> str:
    """Reemplaza claves (``gsk_*``, ``sk-*``, ``*_KEY=...``) por ``***``."""
    redacted = _SECRET_PATTERNS[0].sub("gsk_***", text)
    redacted = _SECRET_PATTERNS[1].sub("sk-***", redacted)
    return _SECRET_PATTERNS[2].sub(r"\1***", redacted)


def user_config_dir() -> Path:
    """Directorio de configuración del usuario (creado si falta)."""
    base = os.environ.get("XDG_CONFIG_HOME", "") or str(Path.home() / ".config")
    directory = Path(base) / APP_DIR_NAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def settings_path() -> Path:
    """Ruta del ``settings.json`` del usuario."""
    return user_config_dir() / SETTINGS_FILE


def env_file_path() -> Path:
    """Ruta del ``.env`` del usuario (claves, modo 600)."""
    return user_config_dir() / ENV_FILE


def save_settings(settings: "AppSettings", path: str | Path | None = None) -> Path:
    """Guarda idiomas, ventanas, dispositivos y knobs (``load_settings`` inverso).

    Nunca persiste claves: ``api_key_env`` guarda solo el NOMBRE de la
    variable, jamás su valor.
    """
    from app.settings.config import AppSettings as _AppSettings

    if not isinstance(settings, _AppSettings):
        raise ConfigurationError("save_settings espera un AppSettings")
    target = Path(path) if path else settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = redact_secrets(json.dumps(settings.to_dict(), indent=2, ensure_ascii=False))
    target.write_text(payload, encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:  # pragma: no cover - plataformas sin chmod
        pass
    logger.info("preferencias guardadas en %s", target)
    return target


def load_user_settings(
    cli_path: str | Path | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> "AppSettings":
    """Base de arranque: ``--config`` > ``settings.json`` del usuario > defecto.

    Luego aplican variables de entorno y CLI como hasta ahora.
    """
    from app.settings.config import AppSettings, load_settings

    if cli_path:
        return load_settings(cli_path, environ=environ)
    user_file = settings_path()
    if user_file.is_file():
        return load_settings(user_file, environ=environ)
    return load_settings(None, environ=environ)


def save_env_key(name: str, value: str, path: str | Path | None = None) -> Path:
    """Guarda una clave en el ``.env`` del usuario con modo 600."""
    target = Path(path) if path else env_file_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if target.is_file():
        lines = [line for line in target.read_text(encoding="utf-8").splitlines() if not line.startswith(f"{name}=")]
    lines.append(f"{name}={value}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(target, 0o600)
    return target


def load_env_file(path: str | Path | None = None) -> dict[str, str]:
    """Lee el ``.env`` del usuario SIN pisar variables ya presentes.

    ``save_env_key`` lo escribía pero nadie lo leía; con esto la clave del
    panel sobrevive a reinicios lanzados desde el menú (sin terminal).
    """
    target = Path(path) if path else env_file_path()
    loaded: dict[str, str] = {}
    if not target.is_file():
        return loaded
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip().strip("'\"")
        if not name:
            continue
        loaded[name] = value
        os.environ.setdefault(name, value)
    return loaded


@dataclass
class ValidationReport:
    """Resultado de ``validate_api_config`` (sin secretos)."""

    provider: str
    key_env: str
    key_present: bool
    models: list[str] = field(default_factory=list)
    ok: bool = False
    detail: str = ""


def validate_api_config(settings: "AppSettings", *, environ: dict[str, str] | None = None) -> list[ValidationReport]:
    """Verifica la configuración de los proveedores (clave + modelos).

    Solo informa presencia de clave y modelos visibles; nunca el secreto.
    Voz: solo Groq (STT). Traducción: los proveedores de IA del registro.
    """
    import os as _os

    from app.translation.providers import provider_for_engine

    env = _os.environ if environ is None else environ
    wanted = {
        "voz": (settings.speech.engine, settings.speech.api_key_env or "GROQ_API_KEY"),
        "traducción": (settings.translation.engine, settings.translation.api_key_env or ""),
    }
    reports: list[ValidationReport] = []
    for _stage, (engine, explicit_env) in wanted.items():
        if engine == "groq" and _stage == "voz":
            key_env = explicit_env or "GROQ_API_KEY"
            reports.append(_validate_groq(key_env, env))
            continue
        provider = provider_for_engine(engine)
        if provider is None:
            continue  # passthrough/mapping: sin IA, nada que validar
        key_env = explicit_env or provider.key_env
        present = bool(env.get(key_env))
        report = ValidationReport(provider=provider.id, key_env=key_env, key_present=present)
        if not present:
            report.detail = f"exportá {key_env}"
            reports.append(report)
            continue
        report = validate_key(provider.id, env.get(key_env), key_env=key_env)
        reports.append(report)
    # Deduplica por variable de clave cuando voz y traducción comparten proveedor.
    seen: dict[str, ValidationReport] = {}
    for report in reports:
        seen.setdefault(report.key_env, report)
    return list(seen.values())


def _validate_groq(key_env: str, env: dict[str, str]) -> ValidationReport:
    """Ruta Groq (SDK oficial) para la voz: presencia de clave + modelos."""
    present = bool(env.get(key_env))
    report = ValidationReport(provider="groq", key_env=key_env, key_present=present)
    if not present:
        report.detail = f"exportá {key_env}"
        return report
    try:
        from groq import Groq

        models = sorted(m.id for m in Groq(api_key=env.get(key_env)).models.list().data)
        report.models = models
        report.ok = True
        report.detail = f"{len(models)} modelos visibles"
    except Exception as exc:
        report.detail = redact_secrets(str(exc))[:200]
    return report


def validate_key(
    provider_id: str,
    api_key: str,
    *,
    key_env: str = "",
    timeout: float = 15.0,
    transport=None,
) -> ValidationReport:
    """Valida UNA clave contra su proveedor listando modelos (sin red si no hay clave).

    ``transport`` permite inyectar ``httpx.MockTransport`` en los tests.
    """
    from app.translation.providers import provider_by_id

    provider = provider_by_id(provider_id)
    key_env = key_env or provider.key_env
    report = ValidationReport(provider=provider.id, key_env=key_env, key_present=bool(api_key))
    if not api_key:
        report.detail = f"exportá {key_env}"
        return report
    try:
        from app.translation.compat import list_models

        models = list_models(provider, api_key, timeout=timeout, transport=transport)
        report.models = models
        report.ok = True
        report.detail = f"{len(models)} modelos visibles"
    except Exception as exc:
        report.detail = redact_secrets(str(exc))[:200]
    return report


def export_logs(
    settings: "AppSettings",
    stats: dict[str, Any] | None,
    dest: str | Path,
    *,
    latency_log: str | Path | None = None,
) -> Path:
    """Guarda diagnóstico redactado: config efectiva, stats y entorno.

    Sin claves (``gsk_*``, ``*_KEY``) y sin audio privado: solo metadatos.
    """
    import app

    dest_dir = Path(dest)
    dest_dir.mkdir(parents=True, exist_ok=True)
    bundle = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "app": {"name": "subtitle-app", "package": getattr(app, "__name__", "app")},
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
        },
        "settings": settings.to_dict(),
        "stats": stats or {},
        "environment": {
            key: redact_secrets(value)
            for key, value in os.environ.items()
            if key.startswith("SUBTITLE_") or key in ("XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP")
        },
    }
    if latency_log:
        source = Path(latency_log)
        if source.is_file():
            marks = []
            for line in source.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    marks.append(json.loads(redact_secrets(line)))
                except json.JSONDecodeError:
                    continue  # líneas corruptas o no JSON se descartan
            bundle["latency_marks"] = marks[-500:]
    out = dest_dir / "diagnostico.json"
    out.write_text(
        redact_secrets(json.dumps(bundle, indent=2, ensure_ascii=False, default=str)),
        encoding="utf-8",
    )
    if latency_log and Path(latency_log).is_file():
        shutil.copy(Path(latency_log), dest_dir / "latency.jsonl")
    logger.info("diagnóstico exportado a %s", dest_dir)
    return out
