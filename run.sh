#!/usr/bin/env bash
#
# Lanza subtítulos en vivo desde el proyecto.
#
# Uso:
#   ./run.sh                     # audio del sistema (monitor) + Groq (usa clave guardada)
#   ./run.sh --audio synthetic --realtime   # modo demo (sin red ni clave)
#   ./run.sh --list-devices      # ver monitores/dispositivos disponibles
#
# Cualquier argumento extra se pasa tal cual a la app.
#
# Las claves se leen de la BD cifrada (~/.config/traductor-tiempo-real/keys.db).
# Si aún no hay ninguna y existe un .env, se migra automáticamente al arrancar.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

VENV_DIR="${VENV_DIR:-.venv}"
PY="$VENV_DIR/bin/python"

if [[ ! -x "$PY" ]]; then
  echo "Error: no encuentro el venv en $VENV_DIR/bin/python" >&2
  echo "Créalo con:" >&2
  echo "  python3 -m venv $VENV_DIR && $VENV_DIR/bin/python -m pip install -r requirements.txt" >&2
  exit 1
fi

# Sin argumentos: arranque grafico sobre el audio del sistema con Groq.
if [[ $# -eq 0 ]]; then
  set -- --renderer qt --audio monitor \
    --speech-engine groq --translation-engine groq --target-languages es
fi

exec "$PY" main.py "$@"
