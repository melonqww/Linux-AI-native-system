#!/usr/bin/env bash
set -euo pipefail

EXTENSION_UUID="ai-native-linux@melonqww"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/../../.." && pwd)"
TARGET_DIR="${HOME}/.local/share/gnome-shell/extensions/${EXTENSION_UUID}"

run_quietly() {
    local output
    if output="$("$@" 2>&1)"; then
        return 0
    fi
    printf '%s\n' "${output}" >&2
    return 1
}

if ! command -v glib-compile-schemas >/dev/null 2>&1; then
    echo "Error: glib-compile-schemas is required to install panel settings" >&2
    exit 1
fi
run_quietly python3 "${SCRIPT_DIR}/validate_extension.py"

if [[ "$(uname -s)" == "Linux" ]]; then
    echo "Connecting the core runtime..."
    run_quietly bash "${REPOSITORY_ROOT}/deployments/systemd/install-user-service.sh"
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
        echo "Error: installed panel file does not match the repository: ${panel_file}" >&2
        exit 1
    fi
done

mkdir -p "${TARGET_DIR}/schemas"
schema_file="org.gnome.shell.extensions.ai-native-linux.gschema.xml"
cp "${SCRIPT_DIR}/schemas/${schema_file}" "${TARGET_DIR}/schemas/${schema_file}"
if ! cmp -s "${SCRIPT_DIR}/schemas/${schema_file}" "${TARGET_DIR}/schemas/${schema_file}"; then
    echo "Error: installed settings schema does not match the repository" >&2
    exit 1
fi
glib-compile-schemas "${TARGET_DIR}/schemas"

rm -rf "${TARGET_DIR}/assets"
cp -R "${SCRIPT_DIR}/assets" "${TARGET_DIR}/assets"
if ! diff -qr "${SCRIPT_DIR}/assets" "${TARGET_DIR}/assets" >/dev/null; then
    echo "Error: installed panel assets do not match the repository" >&2
    exit 1
fi

if command -v gnome-extensions >/dev/null 2>&1; then
    enable_started_at="$(date --iso-8601=seconds)"
    if ! gnome-extensions enable "${EXTENSION_UUID}" >/dev/null; then
        echo "Error: GNOME could not enable ${EXTENSION_UUID}" >&2
        gnome-extensions info "${EXTENSION_UUID}" >&2 || true
        exit 1
    fi
    sleep 2
    if ! gnome-extensions list --enabled | grep -Fxq "${EXTENSION_UUID}"; then
        echo "Error: the extension is not enabled" >&2
        gnome-extensions info "${EXTENSION_UUID}" >&2 || true
        exit 1
    fi
    if command -v journalctl >/dev/null 2>&1; then
        panel_errors="$(journalctl --user --since "${enable_started_at}" --no-pager -o cat 2>/dev/null \
            | grep -F 'AI-native Linux: panel construction failed' || true)"
        if [[ -n "${panel_errors}" ]]; then
            echo "Error: GNOME enabled the extension but could not build the panel:" >&2
            printf '%s\n' "${panel_errors}" >&2
            exit 1
        fi
    fi
else
    echo "Warning: gnome-extensions is unavailable; install gnome-shell-extensions and rerun the installer." >&2
fi

if [[ "$(uname -s)" == "Linux" ]]; then
    runtime_socket="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/ai-native-linux/runtime.sock"
    if [[ ! -S "${runtime_socket}" ]]; then
        echo "Warning: the panel is installed, but the core runtime is not running." >&2
        echo "Rerun: bash apps/desktop-panel/gnome-extension/install.sh" >&2
    fi
    if python3 -c "import gi; gi.require_version('Nautilus', '4.0'); from gi.repository import Nautilus" >/dev/null 2>&1; then
        run_quietly bash "${SCRIPT_DIR}/../nautilus/install.sh"
    fi
fi
