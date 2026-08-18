#!/usr/bin/env bash
set -euo pipefail

EXTENSION_UUID="ai-native-linux@melonqww"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="${HOME}/.local/share/gnome-shell/extensions/${EXTENSION_UUID}"

mkdir -p "${TARGET_DIR}"
cp "${SCRIPT_DIR}/metadata.json" "${TARGET_DIR}/metadata.json"
cp "${SCRIPT_DIR}/extension.js" "${TARGET_DIR}/extension.js"
cp "${SCRIPT_DIR}/stylesheet.css" "${TARGET_DIR}/stylesheet.css"

echo "Установлено в ${TARGET_DIR}"
if command -v gnome-extensions >/dev/null 2>&1; then
    gnome-extensions enable "${EXTENSION_UUID}" || true
    echo "Расширение включено (если текущая сессия разрешает перезагрузку)."
else
    echo "Команда gnome-extensions не найдена. Установите пакет gnome-shell-extensions и повторите запуск."
fi
