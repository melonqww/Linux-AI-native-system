#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DATA_HOME="${XDG_DATA_HOME:-${HOME}/.local/share}"
TARGET_DIR="${DATA_HOME}/nautilus-python/extensions"

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "Интеграция Security Center предназначена только для Linux/Nautilus." >&2
    exit 1
fi

if ! python3 -c "import gi; gi.require_version('Nautilus', '4.0'); from gi.repository import Nautilus"; then
    echo "Не найден Nautilus Python 4. Установите пакет python3-nautilus." >&2
    exit 1
fi

python3 -m py_compile \
    "${SCRIPT_DIR}/security_center_nautilus.py" \
    "${SCRIPT_DIR}/security_center_scan.py"
mkdir -p "${TARGET_DIR}"
cp "${SCRIPT_DIR}/security_center_nautilus.py" "${TARGET_DIR}/security_center_nautilus.py"
cp "${SCRIPT_DIR}/security_center_scan.py" "${TARGET_DIR}/security_center_scan.py"

echo "Интеграция установлена в ${TARGET_DIR}"
echo "Перезапустите Nautilus или войдите в сеанс заново, чтобы увидеть пункт меню."
