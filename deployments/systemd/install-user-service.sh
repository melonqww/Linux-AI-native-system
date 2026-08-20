#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "FAIL: user service can be installed only on Linux" >&2
    exit 1
fi
for command_name in systemctl python3 install; do
    if ! command -v "${command_name}" >/dev/null 2>&1; then
        echo "FAIL: required command is missing: ${command_name}" >&2
        exit 1
    fi
done

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd -- "${script_directory}/../.." && pwd)"
config_home="${XDG_CONFIG_HOME:-${HOME}/.config}"
user_data_home="${XDG_DATA_HOME:-${HOME}/.local/share}"
unit_directory="${config_home}/systemd/user"
runtime_config_directory="${config_home}/ai-native-linux"
libexec_directory="${HOME}/.local/libexec/ai-native-linux"

install -d -m 0700 "${runtime_config_directory}"
install -d -m 0755 "${unit_directory}" "${libexec_directory}"
install -m 0755 "${script_directory}/run-runtime.sh" "${libexec_directory}/run-runtime.sh"
install -m 0644 \
    "${script_directory}/ai-native-linux-runtime.service" \
    "${unit_directory}/ai-native-linux-runtime.service"
printf '%s\n' "${repository_root}" > "${runtime_config_directory}/repository-root"
chmod 0600 "${runtime_config_directory}/repository-root"

environment_file="${runtime_config_directory}/runtime.env"
if [[ ! -e "${environment_file}" ]]; then
    printf '%s\n' \
        'AI_NATIVE_INTENT_MODEL=qwen3:1.7b' \
        'AI_NATIVE_OLLAMA_URL=http://127.0.0.1:11434' \
        > "${environment_file}"
    chmod 0600 "${environment_file}"
fi
install -d -m 0700 "${user_data_home}/ai-native-linux"

runtime_data_directory="${user_data_home}/ai-native-linux"
virtual_environment="${runtime_data_directory}/venv"
requirements_file="${script_directory}/runtime-requirements.txt"

if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 12))'; then
    echo "FAIL: Python 3.12 or newer is required" >&2
    exit 1
fi
if [[ ! -x "${virtual_environment}/bin/python" ]]; then
    echo "Preparing isolated Python environment..."
    if ! python3 -m venv "${virtual_environment}"; then
        echo "FAIL: Python could not create a virtual environment" >&2
        echo "On Ubuntu install it with: sudo apt install python3-venv" >&2
        exit 1
    fi
fi
if ! "${virtual_environment}/bin/python" -m pip install \
    --disable-pip-version-check \
    --requirement "${requirements_file}"; then
    echo "FAIL: runtime Python dependencies could not be installed" >&2
    echo "Check the network connection and rerun this installer." >&2
    exit 1
fi
if ! "${virtual_environment}/bin/python" -c 'import pypdf'; then
    echo "FAIL: runtime dependency verification failed: pypdf" >&2
    exit 1
fi

systemctl --user daemon-reload
systemctl --user reset-failed ai-native-linux-runtime.service || true
systemctl --user enable --now ai-native-linux-runtime.service

socket_path="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/ai-native-linux/runtime.sock"
for _attempt in {1..40}; do
    if [[ -S "${socket_path}" ]]; then
        echo "PASS: runtime service is active"
        echo "PASS: Unix socket ${socket_path}"
        echo "RESULT: PANEL CORE CONNECTED"
        exit 0
    fi
    if ! systemctl --user is-active --quiet ai-native-linux-runtime.service; then
        break
    fi
    sleep 0.5
done

echo "FAIL: runtime did not create its Unix socket" >&2
systemctl --user --no-pager --full status ai-native-linux-runtime.service >&2 || true
echo "Diagnostics: journalctl --user -u ai-native-linux-runtime.service -n 100 --no-pager" >&2
exit 1
