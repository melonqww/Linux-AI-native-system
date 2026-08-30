#!/usr/bin/env bash
set -euo pipefail

config_home="${XDG_CONFIG_HOME:-${HOME}/.config}"
data_home="${XDG_DATA_HOME:-${HOME}/.local/share}"
repository_file="${config_home}/ai-native-linux/repository-root"

if [[ ! -f "${repository_file}" ]]; then
    echo "Repository location is not configured: ${repository_file}" >&2
    exit 1
fi
IFS= read -r repository_root < "${repository_file}"
if [[ -z "${repository_root}" || "${repository_root}" != /* || ! -d "${repository_root}" ]]; then
    echo "Configured repository root is invalid" >&2
    exit 1
fi
if [[ ! -f "${repository_root}/services/agent-runtime/src/ai_native_linux/cli.py" ]]; then
    echo "Configured repository does not contain AI-native Linux runtime" >&2
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required" >&2
    exit 1
fi

runtime_data="${data_home}/ai-native-linux"
install -d -m 0700 "${runtime_data}"
runtime_python="${runtime_data}/venv/bin/python"
if [[ ! -x "${runtime_python}" ]]; then
    echo "Runtime Python environment is missing; rerun deployments/systemd/install-user-service.sh" >&2
    exit 1
fi

source_paths=(
    "services/agent-runtime/src"
    "services/task-ledger/src"
    "services/workspace-service/src"
    "services/turn-router/src"
    "services/permission-gateway/src"
    "services/execution-orchestrator/src"
    "services/intent-compiler/src"
    "services/capability-registry/src"
    "services/module-manager/src"
    "services/query-service/src"
    "services/storage-catalog/src"
    "services/indexer/src"
    "services/index-scheduler/src"
    "modules/documents-pdf/src"
    "modules/system-monitor/src"
    "modules/software-manager/src"
)
runtime_pythonpath=""
for relative_path in "${source_paths[@]}"; do
    absolute_path="${repository_root}/${relative_path}"
    if [[ ! -d "${absolute_path}" ]]; then
        echo "Required source path is missing: ${relative_path}" >&2
        exit 1
    fi
    runtime_pythonpath="${runtime_pythonpath:+${runtime_pythonpath}:}${absolute_path}"
done
export PYTHONPATH="${runtime_pythonpath}${PYTHONPATH:+:${PYTHONPATH}}"

intent_model="${AI_NATIVE_INTENT_MODEL:-qwen3.5:2b}"
ollama_url="${AI_NATIVE_OLLAMA_URL:-http://127.0.0.1:11434}"

cd -- "${repository_root}"
if ! "${runtime_python}" -c 'import pypdf, zstandard'; then
    echo "Runtime Python dependencies are incomplete; rerun deployments/systemd/install-user-service.sh" >&2
    exit 1
fi
exec "${runtime_python}" -m ai_native_linux.cli \
    --serve-panel \
    --transport unix \
    --storage-database "${runtime_data}/storage-catalog.sqlite3" \
    --index-database "${runtime_data}/document-index.sqlite3" \
    --registry-database "${runtime_data}/capabilities.sqlite3" \
    --task-ledger-database "${runtime_data}/task-ledger.sqlite3" \
    --memory-database "${runtime_data}/task-memory.sqlite3" \
    --workspace-database "${runtime_data}/workspace.sqlite3" \
    --audit-file "${runtime_data}/audit-events.jsonl" \
    --intent-model "${intent_model}" \
    --ollama-url "${ollama_url}"
