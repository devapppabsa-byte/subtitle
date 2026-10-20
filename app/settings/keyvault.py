"""Almacén de claves de proveedores cifradas en SQLite (Fase 9).

Una fila por proveedor. El secreto NUNCA se guarda en claro: se cifra con una
clave maestra de 32 bytes que vive en el **llavero del sistema** (``secret-tool``
/ libsecret). El cifrado es un keystream XOR autenticado construido solo con la
stdlib (HMAC-SHA256 en modo contador); sin dependencias externas.

Diseño:

* ``provider_keys`` — ``provider_id`` PK, ``key_env``, ``ciphertext`` (BLOB),
  ``created_at``/``updated_at``. Nunca el texto plano.
* ``model_cache`` — últimos modelos vistos por proveedor (para no re-consultar).

Si el llavero no está disponible, las claves NO se persisten: se avisa y se
mantienen solo en memoria durante la sesión (nunca en claro en disco).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from app.core.logging import get_logger

__all__ = [
    "KeyVault",
    "VaultUnavailable",
    "db_path",
    "default_vault",
]

logger = get_logger(__name__)

DB_FILE = "keys.db"
_MASTER_BYTES = 32
_NONCE_BYTES = 16
_SECRET_SERVICE = "subtitle-app"
_SECRET_KEY = "master"
_SECRET_LABEL = "subtitle-app master key (API keys at rest)"


class VaultUnavailable(RuntimeError):
    """El llavero del sistema no está disponible (secret-tool ausente/falla)."""


# ---------------------------------------------------------------------------
# Cifrado (stdlib): keystream XOR + HMAC (encrypt-then-MAC)
# ---------------------------------------------------------------------------
def _keystream(master: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(master, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def encrypt(master: bytes, plaintext: str) -> bytes:
    """``nonce || ciphertext || tag`` (tag = HMAC-SHA256(master, nonce||ct))."""
    data = plaintext.encode("utf-8")
    nonce = secrets.token_bytes(_NONCE_BYTES)
    cipher = bytes(a ^ b for a, b in zip(data, _keystream(master, nonce, len(data))))
    tag = hmac.new(master, nonce + cipher, hashlib.sha256).digest()
    return nonce + cipher + tag


def decrypt(master: bytes, blob: bytes) -> str:
    """Descifra y verifica integridad; lanza ``ValueError`` si el tag no cuadra."""
    if len(blob) < _NONCE_BYTES + hashlib.sha256().digest_size:
        raise ValueError("blob cifrado demasiado corto")
    nonce = blob[:_NONCE_BYTES]
    tag = blob[-32:]
    cipher = blob[_NONCE_BYTES:-32]
    expected = hmac.new(master, nonce + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise ValueError("tag HMAC inválido: dato corrupto o clave maestra distinta")
    data = bytes(a ^ b for a, b in zip(cipher, _keystream(master, nonce, len(cipher))))
    return data.decode("utf-8")


# ---------------------------------------------------------------------------
# Llavero del sistema
# ---------------------------------------------------------------------------
class _SecretToolLlavero:
    """Clave maestra gestionada por ``secret-tool`` (libsecret)."""

    def available(self) -> bool:
        return shutil.which("secret-tool") is not None

    def load(self) -> bytes | None:
        try:
            result = subprocess.run(
                ["secret-tool", "lookup", "service", _SECRET_SERVICE, "key", _SECRET_KEY],
                capture_output=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise VaultUnavailable(str(exc)) from exc
        if result.returncode != 0 or not result.stdout.strip():
            return None
        try:
            return base64.b64decode(result.stdout.strip())
        except ValueError as exc:
            raise VaultUnavailable("clave maestra corrupta en el llavero") from exc

    def store(self, master: bytes) -> None:
        payload = base64.b64encode(master).decode("ascii")
        try:
            result = subprocess.run(
                ["secret-tool", "store", "--label", _SECRET_LABEL,
                 "service", _SECRET_SERVICE, "key", _SECRET_KEY],
                input=payload.encode("ascii"), capture_output=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise VaultUnavailable(str(exc)) from exc
        if result.returncode != 0:
            raise VaultUnavailable(
                (result.stderr or b"").decode("utf-8", "replace").strip() or "secret-tool store falló"
            )


# ---------------------------------------------------------------------------
# KeyVault
# ---------------------------------------------------------------------------
def db_path() -> Path:
    """Ruta de la BD de claves (respeta XDG_CONFIG_HOME)."""
    from app.settings.store import user_config_dir

    return user_config_dir() / DB_FILE


def default_vault() -> "KeyVault":
    """Vault sobre la BD del usuario y el llavero del sistema."""
    return KeyVault(db_path())


class KeyVault:
    """Claves por proveedor en SQLite, cifradas con una clave maestra del llavero."""

    def __init__(self, path: str | Path, *, llavero=None, master: bytes | None = None) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._llavero = llavero if llavero is not None else _SecretToolLlavero()
        self._master = master
        self._persistable = True
        self._ensure_schema()

    # -- esquema -----------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS provider_keys (
                    provider_id TEXT PRIMARY KEY,
                    key_env     TEXT NOT NULL,
                    ciphertext  BLOB NOT NULL,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_cache (
                    provider_id TEXT NOT NULL,
                    model_id    TEXT NOT NULL,
                    free        INTEGER NOT NULL DEFAULT 0,
                    fetched_at  TEXT NOT NULL,
                    PRIMARY KEY (provider_id, model_id)
                );
                """
            )
        self._chmod()

    def _chmod(self) -> None:
        try:
            os.chmod(self._path, 0o600)
        except OSError:  # pragma: no cover - plataformas sin chmod
            pass

    # -- clave maestra -----------------------------------------------------
    @property
    def persistable(self) -> bool:
        """``False`` si el llavero no está disponible (claves solo en sesión)."""
        return self._persistable and self._master is not None

    def _master_key(self) -> bytes | None:
        if self._master is not None:
            return self._master
        if not self._llavero.available():
            self._persistable = False
            logger.warning(
                "llavero del sistema no disponible (secret-tool); las claves no se "
                "persistirán: solo vivirán en memoria durante esta sesión"
            )
            return None
        try:
            master = self._llavero.load()
            if master is None:
                master = secrets.token_bytes(_MASTER_BYTES)
                self._llavero.store(master)
                logger.info("clave maestra creada en el llavero del sistema")
            elif len(master) != _MASTER_BYTES:
                raise VaultUnavailable("clave maestra con longitud inesperada")
        except VaultUnavailable as exc:
            self._persistable = False
            logger.warning("no se pudo usar el llavero (%s); claves solo en memoria", exc)
            return None
        self._master = master
        return master

    # -- API de claves -----------------------------------------------------
    def save_key(self, provider_id: str, key_env: str, api_key: str) -> bool:
        """Guarda (cifrada) la clave del proveedor. Devuelve ``False`` si no persiste."""
        if not api_key:
            raise ValueError("api_key vacía")
        master = self._master_key()
        if master is None:
            return False  # en memoria: el runtime ya la puso en os.environ
        blob = encrypt(master, api_key)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO provider_keys (provider_id, key_env, ciphertext, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(provider_id) DO UPDATE SET
                    key_env=excluded.key_env, ciphertext=excluded.ciphertext, updated_at=excluded.updated_at
                """,
                (provider_id, key_env, blob, now, now),
            )
        self._chmod()
        logger.info("clave de %s guardada cifrada en %s", provider_id, self._path.name)
        return True

    def load_key(self, provider_id: str) -> str | None:
        """Devuelve la clave en claro SOLO en memoria, o ``None``."""
        master = self._master_key()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ciphertext FROM provider_keys WHERE provider_id = ?", (provider_id,)
            ).fetchone()
        if row is None:
            return None
        if master is None:
            logger.warning("hay clave de %s pero el llavero no está disponible", provider_id)
            return None
        try:
            return decrypt(master, bytes(row["ciphertext"]))
        except ValueError:
            logger.exception("no se pudo descifrar la clave de %s", provider_id)
            return None

    def providers_with_key(self) -> list[str]:
        """Ids de proveedores con clave guardada."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT provider_id FROM provider_keys ORDER BY provider_id"
            ).fetchall()
        return [row["provider_id"] for row in rows]

    def delete_key(self, provider_id: str) -> None:
        """Elimina la clave del proveedor (idempotente)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM provider_keys WHERE provider_id = ?", (provider_id,))

    def load_all_into_environ(self, models: dict[str, str] | None = None) -> dict[str, str]:
        """Inyecta las claves guardadas en ``os.environ`` (sin pisar lo ya presente).

        ``models`` mapea ``provider_id -> key_env`` para derivar la variable; si
        se omite, se usa el ``key_env`` guardado en la propia fila.
        """
        loaded: dict[str, str] = {}
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT provider_id, key_env FROM provider_keys"
            ).fetchall()
        for row in rows:
            key_env = (models or {}).get(row["provider_id"]) or row["key_env"]
            secret = self.load_key(row["provider_id"])
            if secret:
                os.environ.setdefault(key_env, secret)
                loaded[key_env] = secret
        return loaded

    # -- caché de modelos --------------------------------------------------
    def cache_models(self, provider_id: str, models: list[tuple[str, bool]]) -> None:
        """Guarda ``[(model_id, free)]`` para no re-consultar cada vez."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("DELETE FROM model_cache WHERE provider_id = ?", (provider_id,))
            conn.executemany(
                "INSERT OR REPLACE INTO model_cache (provider_id, model_id, free, fetched_at) "
                "VALUES (?, ?, ?, ?)",
                [(provider_id, mid, 1 if free else 0, now) for mid, free in models],
            )

    def cached_models(self, provider_id: str) -> list[tuple[str, bool]]:
        """Modelos cacheados ``[(model_id, free)]`` (vacíos si no hay)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT model_id, free FROM model_cache WHERE provider_id = ? ORDER BY model_id",
                (provider_id,),
            ).fetchall()
        return [(row["model_id"], bool(row["free"])) for row in rows]

    # -- migración ---------------------------------------------------------
    def migrate_from_env_file(self, env_file: str | Path | None = None) -> int:
        """Importa claves del ``.env`` antiguo a la BD cifrada y vacía el fichero.

        Devuelve cuántas claves migró. Idempotente: un ``.env`` ya vacío no
        cambia nada.
        """
        from app.settings.store import env_file_path, load_env_file
        from app.translation.providers import PROVIDERS

        target = Path(env_file) if env_file else env_file_path()
        if not target.is_file():
            return 0
        values = load_env_file(target)
        id_to_env = {p.id: p.key_env for p in PROVIDERS.values()}
        env_to_id = {p.key_env: p.id for p in PROVIDERS.values()}
        migrated = 0
        for key_env, value in values.items():
            provider_id = env_to_id.get(key_env)
            if not provider_id or not value:
                continue
            if self.save_key(provider_id, id_to_env[provider_id], value):
                migrated += 1
        if migrated and self.persistable:
            try:
                target.unlink()  # las claves ya viven cifradas en la BD
            except OSError:  # pragma: no cover
                logger.warning("no se pudo eliminar el .env migrado")
        return migrated
