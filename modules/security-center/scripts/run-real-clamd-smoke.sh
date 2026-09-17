#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/../../.." && pwd)"
CLAMD_USER="${AI_NATIVE_CLAMD_USER:-clamav}"
CLAMD_BIN="$(command -v clamd || true)"

if [[ -z "${CLAMD_BIN}" && -x /usr/sbin/clamd ]]; then
  CLAMD_BIN=/usr/sbin/clamd
fi
if [[ -z "${CLAMD_BIN}" ]]; then
  echo "ERROR: clamd is not installed" >&2
  exit 2
fi
if ! getent passwd "${CLAMD_USER}" >/dev/null; then
  echo "ERROR: clamd service user '${CLAMD_USER}' does not exist" >&2
  exit 2
fi

CLAMD_GROUP="$(id -gn "${CLAMD_USER}")"
CLAMD_UID="$(id -u "${CLAMD_USER}")"
TEMP_BASE="${TMPDIR:-/tmp}"
RUNTIME_ROOT="$(mktemp -d "${TEMP_BASE%/}/ai-native-clamd.XXXXXX")"
case "${RUNTIME_ROOT}" in
  "${TEMP_BASE%/}"/ai-native-clamd.*) ;;
  *) echo "ERROR: unsafe temporary path" >&2; exit 2 ;;
esac

cleanup() {
  if [[ -f "${RUNTIME_ROOT}/clamd.pid" ]]; then
    daemon_pid="$(sudo cat "${RUNTIME_ROOT}/clamd.pid" 2>/dev/null || true)"
    if [[ "${daemon_pid}" =~ ^[0-9]+$ ]]; then
      sudo kill "${daemon_pid}" 2>/dev/null || true
    fi
  fi
  sudo rm -rf -- "${RUNTIME_ROOT}"
}
trap cleanup EXIT

sudo chown "${CLAMD_USER}:${CLAMD_GROUP}" "${RUNTIME_ROOT}"
sudo chmod 0755 "${RUNTIME_ROOT}"
sudo install -d -o "${CLAMD_USER}" -g "${CLAMD_GROUP}" -m 0750 \
  "${RUNTIME_ROOT}/database" "${RUNTIME_ROOT}/tmp"

SIGNATURE_HEX="41495f4e41544956455f53454355524954595f5245414c5f434c414d445f53594e5448455449435f5631"
printf 'AiNative-Test-Synthetic:0:*:%s\n' "${SIGNATURE_HEX}" \
  | sudo tee "${RUNTIME_ROOT}/database/ai-native-test.ndb" >/dev/null

sudo tee "${RUNTIME_ROOT}/clamd.conf" >/dev/null <<EOF
DatabaseDirectory ${RUNTIME_ROOT}/database
LocalSocket ${RUNTIME_ROOT}/clamd.sock
LocalSocketMode 666
FixStaleSocket yes
PidFile ${RUNTIME_ROOT}/clamd.pid
LogFile ${RUNTIME_ROOT}/clamd.log
LogTime yes
MaxThreads 2
MaxQueue 4
ReadTimeout 10
CommandReadTimeout 5
StreamMaxLength 4M
TemporaryDirectory ${RUNTIME_ROOT}/tmp
EOF
sudo chown -R "${CLAMD_USER}:${CLAMD_GROUP}" "${RUNTIME_ROOT}"

sudo -u "${CLAMD_USER}" "${CLAMD_BIN}" --config-file="${RUNTIME_ROOT}/clamd.conf"
for _attempt in {1..50}; do
  [[ -S "${RUNTIME_ROOT}/clamd.sock" ]] && break
  sleep 0.1
done
if [[ ! -S "${RUNTIME_ROOT}/clamd.sock" ]]; then
  sudo cat "${RUNTIME_ROOT}/clamd.log" >&2 || true
  echo "ERROR: clamd socket was not created" >&2
  exit 1
fi

export AI_NATIVE_RUN_REAL_CLAMD=1
export AI_NATIVE_REAL_CLAMD_SOCKET="${RUNTIME_ROOT}/clamd.sock"
export AI_NATIVE_REAL_CLAMD_UID="${CLAMD_UID}"
cd "${PROJECT_ROOT}"
python -m pytest modules/security-center/tests/test_real_clamd_smoke.py -q
