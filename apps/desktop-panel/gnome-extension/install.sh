#!/usr/bin/env bash
set -euo pipefail

EXTENSION_UUID="ai-native-linux@melonqww"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/../../.." && pwd)"
TARGET_DIR="${HOME}/.local/share/gnome-shell/extensions/${EXTENSION_UUID}"

if ! command -v glib-compile-schemas >/dev/null 2>&1; then
    echo "Ошибка: для установки настроек нужен glib-compile-schemas" >&2
    exit 1
fi
python3 "${SCRIPT_DIR}/validate_extension.py"

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

mkdir -p "${TARGET_DIR}/schemas"
schema_file="org.gnome.shell.extensions.ai-native-linux.gschema.xml"
cp "${SCRIPT_DIR}/schemas/${schema_file}" "${TARGET_DIR}/schemas/${schema_file}"
if ! cmp -s "${SCRIPT_DIR}/schemas/${schema_file}" "${TARGET_DIR}/schemas/${schema_file}"; then
    echo "Ошибка: схема настроек расширения не совпадает" >&2
    exit 1
fi
glib-compile-schemas "${TARGET_DIR}/schemas"

rm -rf "${TARGET_DIR}/assets"
cp -R "${SCRIPT_DIR}/assets" "${TARGET_DIR}/assets"
if ! diff -qr "${SCRIPT_DIR}/assets" "${TARGET_DIR}/assets" >/dev/null; then
    echo "Ошибка: установленные ресурсы панели не совпадают с репозиторием" >&2
    exit 1
fi

echo "Установлено в ${TARGET_DIR}"
echo "PASS: файлы панели побайтно совпадают с репозиторием"
if command -v gnome-extensions >/dev/null 2>&1; then
    enable_started_at="$(date --iso-8601=seconds)"
    if ! gnome-extensions enable "${EXTENSION_UUID}"; then
        echo "Ошибка: GNOME не включил расширение ${EXTENSION_UUID}" >&2
        gnome-extensions info "${EXTENSION_UUID}" >&2 || true
        exit 1
    fi
    sleep 2
    if ! gnome-extensions list --enabled | grep -Fxq "${EXTENSION_UUID}"; then
        echo "Ошибка: расширение не перешло в состояние enabled" >&2
        gnome-extensions info "${EXTENSION_UUID}" >&2 || true
        exit 1
    fi
    if command -v journalctl >/dev/null 2>&1; then
        panel_errors="$(journalctl --user --since "${enable_started_at}" --no-pager -o cat 2>/dev/null \
            | grep -F 'AI-native Linux: panel construction failed' || true)"
        if [[ -n "${panel_errors}" ]]; then
            echo "Ошибка: GNOME включил расширение, но не смог построить панель:" >&2
            printf '%s\n' "${panel_errors}" >&2
            exit 1
        fi
    fi
    echo "Расширение включено."
else
    echo "Команда gnome-extensions не найдена. Установите пакет gnome-shell-extensions и повторите запуск."
fi

echo "Если GNOME продолжает показывать старую панель, выйдите из сеанса и войдите снова."

if [[ "$(uname -s)" == "Linux" ]]; then
    runtime_socket="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/ai-native-linux/runtime.sock"
    if [[ ! -S "${runtime_socket}" ]]; then
        echo "Внимание: панель установлена, но ядро не запущено."
        echo "Повторите установку панели: bash apps/desktop-panel/gnome-extension/install.sh"
    fi
    if python3 -c "import gi; gi.require_version('Nautilus', '4.0'); from gi.repository import Nautilus" >/dev/null 2>&1; then
        bash "${SCRIPT_DIR}/../nautilus/install.sh"
    else
        echo "Пункт контекстного меню Nautilus не установлен: нужен пакет python3-nautilus."
    fi
fi
