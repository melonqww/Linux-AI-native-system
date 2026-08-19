#!/usr/bin/env bash
set -euo pipefail

EXTENSION_UUID="ai-native-linux@melonqww"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

for command_name in gnome-shell gnome-extensions journalctl; do
    if ! command -v "${command_name}" >/dev/null 2>&1; then
        echo "FAIL: не найдена команда ${command_name}"
        exit 1
    fi
done

shell_version="$(gnome-shell --version)"
if [[ ! "${shell_version}" =~ GNOME[[:space:]]Shell[[:space:]](46|47|48)(\.|$) ]]; then
    echo "FAIL: нужен GNOME Shell 46–48, найдено: ${shell_version}"
    exit 1
fi

started_at="$(date --iso-8601=seconds)"
"${SCRIPT_DIR}/install.sh"
gnome-extensions enable "${EXTENSION_UUID}"
sleep 3

if ! gnome-extensions list --enabled | grep -Fxq "${EXTENSION_UUID}"; then
    echo "FAIL: расширение не перешло в enabled"
    gnome-extensions info "${EXTENSION_UUID}" || true
    exit 1
fi

shell_log="$(journalctl --user --since "${started_at}" --no-pager -o cat 2>/dev/null || true)"
extension_log="$(printf '%s\n' "${shell_log}" | grep -F "${EXTENSION_UUID}" || true)"
if printf '%s\n' "${extension_log}" | grep -Eiq 'JS ERROR|exception|traceback|error:'; then
    echo "FAIL: GNOME Shell зарегистрировал ошибку расширения"
    printf '%s\n' "${extension_log}"
    exit 1
fi

echo "PASS: ${shell_version}"
echo "PASS: расширение enabled"
echo "PASS: новых ошибок ${EXTENSION_UUID} в journal нет"
echo "RESULT: READY FOR VISUAL CHECK"
