#!/usr/bin/env bash
set -euo pipefail

EXTENSION_UUID="ai-native-linux@melonqww"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/../../.." && pwd)"
TARGET_DIR="${HOME}/.local/share/gnome-shell/extensions/${EXTENSION_UUID}"

if [[ "$(uname -s)" == "Linux" ]]; then
    echo "Обновляю и подключаю runtime ядра..."
    bash "${REPOSITORY_ROOT}/deployments/systemd/install-user-service.sh"
fi

mkdir -p "${TARGET_DIR}"

if command -v gnome-extensions >/dev/null 2>&1; then
    # Stop the live module before replacing its files.  Otherwise GNOME Shell
    # can keep the previous JavaScript object tree until the next session.
    gnome-extensions disable "${EXTENSION_UUID}" >/dev/null 2>&1 || true
    sleep 1
fi

panel_files=(metadata.json extension.js runtime-client.js panel-presenter.js stylesheet.css)
for panel_file in "${panel_files[@]}"; do
    cp "${SCRIPT_DIR}/${panel_file}" "${TARGET_DIR}/${panel_file}"
    if ! cmp -s "${SCRIPT_DIR}/${panel_file}" "${TARGET_DIR}/${panel_file}"; then
        echo "Ошибка: установленный файл панели не совпадает: ${panel_file}" >&2
        exit 1
    fi
done

rm -rf "${TARGET_DIR}/assets"
cp -R "${SCRIPT_DIR}/assets" "${TARGET_DIR}/assets"
if ! diff -qr "${SCRIPT_DIR}/assets" "${TARGET_DIR}/assets" >/dev/null; then
    echo "Ошибка: установленные ресурсы панели не совпадают с репозиторием" >&2
    exit 1
fi

echo "Установлено в ${TARGET_DIR}"
echo "PASS: файлы панели побайтно совпадают с репозиторием"
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
        echo "Повторите установку панели: bash apps/desktop-panel/gnome-extension/install.sh"
    fi
fi
