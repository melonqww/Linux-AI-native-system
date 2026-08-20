#!/usr/bin/env bash
set -euo pipefail

config_home="${XDG_CONFIG_HOME:-${HOME}/.config}"
unit_path="${config_home}/systemd/user/ai-native-linux-runtime.service"
launcher_path="${HOME}/.local/libexec/ai-native-linux/run-runtime.sh"

systemctl --user disable --now ai-native-linux-runtime.service >/dev/null 2>&1 || true
rm -f -- "${unit_path}" "${launcher_path}"
systemctl --user daemon-reload
systemctl --user reset-failed ai-native-linux-runtime.service >/dev/null 2>&1 || true

echo "Runtime user service removed. Databases and runtime.env were preserved."
