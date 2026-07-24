#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

PYTHON_BIN="${PYTHON_BIN:-python}"
# Use the same weights directory as the post-cleaning pipeline. This package is
# nested one level deeper under data_clean, so the shared model is two levels up.
MODEL_PATH="${MODEL_PATH:-${PACKAGE_ROOT}/../../../models/Qwen3-32B}"
API_MODEL_NAME="${API_MODEL_NAME:-Qwen3-32B}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-32768}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.88}"
MAX_NUM_SEQS="${MAX_NUM_SEQS:-16}"
HOST="${HOST:-127.0.0.1}"
STARTUP_TIMEOUT="${STARTUP_TIMEOUT:-1800}"

LOG_DIR="${PACKAGE_ROOT}/logs/vllm"
PID_DIR="${PACKAGE_ROOT}/run"
mkdir -p "${LOG_DIR}" "${PID_DIR}" "${PACKAGE_ROOT}/output"

cleanup_on_start_failure() {
  local exit_code=$?
  trap - ERR
  echo "Startup did not complete; cleaning up vLLM processes recorded by this invocation." >&2
  bash "${SCRIPT_DIR}/stop_vllm_8gpu.sh" || true
  exit "${exit_code}"
}
trap cleanup_on_start_failure ERR

if [[ ! -e "${MODEL_PATH}" ]]; then
  echo "ERROR: model directory not found: ${MODEL_PATH}" >&2
  echo "Set MODEL_PATH=/actual/path/Qwen3-32B and retry." >&2
  exit 1
fi

wait_until_ready() {
  local port="$1"
  local pid="$2"
  local deadline=$((SECONDS + STARTUP_TIMEOUT))

  while (( SECONDS < deadline )); do
    if ! kill -0 "${pid}" 2>/dev/null; then
      echo "ERROR: the vLLM process for port ${port} exited. Log tail:" >&2
      tail -n 80 "${LOG_DIR}/vllm-${port}.log" >&2 || true
      return 1
    fi
    if curl -fsS --max-time 5 "http://127.0.0.1:${port}/v1/models" >/dev/null 2>&1; then
      echo "Port ${port} is ready."
      return 0
    fi
    sleep 5
  done

  echo "ERROR: timed out after ${STARTUP_TIMEOUT} seconds while waiting for port ${port}." >&2
  return 1
}

start_instance() {
  local gpu_group="$1"
  local port="$2"
  local pid_file="${PID_DIR}/vllm-${port}.pid"
  local log_file="${LOG_DIR}/vllm-${port}.log"

  if [[ -f "${pid_file}" ]]; then
    local old_pid
    old_pid="$(<"${pid_file}")"
    if [[ "${old_pid}" =~ ^[0-9]+$ ]] && kill -0 "${old_pid}" 2>/dev/null; then
      echo "ERROR: recorded process ${old_pid} for port ${port} is still running; run the stop script first." >&2
      return 1
    fi
    rm -f -- "${pid_file}"
  fi

  echo "Starting GPUs ${gpu_group} -> port ${port} (TP=4)"
  CUDA_VISIBLE_DEVICES="${gpu_group}" nohup "${PYTHON_BIN}" -m vllm.entrypoints.openai.api_server \
    --model "${MODEL_PATH}" \
    --served-model-name "${API_MODEL_NAME}" \
    --host "${HOST}" \
    --port "${port}" \
    --tensor-parallel-size 4 \
    --dtype auto \
    --max-model-len "${MAX_MODEL_LEN}" \
    --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}" \
    --max-num-seqs "${MAX_NUM_SEQS}" \
    --enable-prefix-caching \
    --disable-log-requests \
    >"${log_file}" 2>&1 &

  local pid=$!
  printf '%s\n' "${pid}" >"${pid_file}"
  wait_until_ready "${port}" "${pid}"
}

echo "Model directory: ${MODEL_PATH}"
echo "Served model name: ${API_MODEL_NAME}"
echo "Context=${MAX_MODEL_LEN}, GPU memory utilization=${GPU_MEMORY_UTILIZATION}, max_num_seqs=${MAX_NUM_SEQS}"

# Sequential startup avoids the extra host-memory peak from loading both 32B replicas at once.
start_instance "0,1,2,3" 8000
start_instance "4,5,6,7" 8001

trap - ERR

echo "Both TP4 endpoints are ready:"
echo "  http://127.0.0.1:8000/v1"
echo "  http://127.0.0.1:8001/v1"
