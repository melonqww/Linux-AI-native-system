#!/usr/bin/env bash
set -euo pipefail

EXTENSION_UUID="ai-native-linux@melonqww"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="${HOME}/.local/share/gnome-shell/extensions/${EXTENSION_UUID}"

mkdir -p "${TARGET_DIR}"

if command -v gnome-extensions >/dev/null 2>&1; then
    # Stop the live module before replacing its files.  Otherwise GNOME Shell
    # can keep the previous JavaScript object tree until the next session.
    gnome-extensions disable "${EXTENSION_UUID}" >/dev/null 2>&1 || true
    sleep 1
fi

cp "${SCRIPT_DIR}/metadata.json" "${TARGET_DIR}/metadata.json"
cp "${SCRIPT_DIR}/extension.js" "${TARGET_DIR}/extension.js"
cp "${SCRIPT_DIR}/runtime-client.js" "${TARGET_DIR}/runtime-client.js"
cp "${SCRIPT_DIR}/panel-presenter.js" "${TARGET_DIR}/panel-presenter.js"
cp "${SCRIPT_DIR}/stylesheet.css" "${TARGET_DIR}/stylesheet.css"

echo "Установлено в ${TARGET_DIR}"
if command -v gnome-extensions >/dev/null 2>&1; then
    gnome-extensions enable "${EXTENSION_UUID}" || true
    echo "Расширение включено (если текущая сессия разрешает перезагрузку)."
else
    echo "Команда gnome-extensions не найдена. Установите пакет gnome-shell-extensions и повторите запуск."
fi

if [[ "$(uname -s)" == "Linux" ]]; then
    runtime_socket="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/ai-native-linux/runtime.sock"
    if [[ ! -S "${runtime_socket}" ]]; then
        echo "Внимание: панель установлена, но ядро не запущено."
        echo "Запустите: ./deployments/systemd/install-user-service.sh"
    fi
fi
