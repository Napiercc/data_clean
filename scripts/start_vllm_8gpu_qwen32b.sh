#!/usr/bin/env bash
set -euo pipefail

MODEL="${MODEL:-../models/Qwen3-32B}"
HOST="${HOST:-0.0.0.0}"
BASE_PORT="${BASE_PORT:-8000}"
NUM_GPUS="${NUM_GPUS:-8}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-4096}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.85}"
PID_DIR="${PID_DIR:-run/vllm_pids}"
LOG_DIR="${LOG_DIR:-logs/vllm}"

mkdir -p "$PID_DIR" "$LOG_DIR"

wait_for_server() {
  local port="$1"
  local log_file="$2"
  for _ in $(seq 1 120); do
    if python - "$port" <<'PY' >/dev/null 2>&1
import sys
import urllib.request

port = sys.argv[1]
try:
    urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=2).read()
except Exception:
    raise SystemExit(1)
PY
    then
      return 0
    fi
    sleep 5
  done
  echo "vLLM server on port ${port} did not become ready. Last log lines:" >&2
  tail -n 80 "$log_file" >&2 || true
  return 1
}

for gpu in $(seq 0 $((NUM_GPUS - 1))); do
  port=$((BASE_PORT + gpu))
  log_file="${LOG_DIR}/vllm_gpu${gpu}_port${port}.log"
  pid_file="${PID_DIR}/vllm_gpu${gpu}_port${port}.pid"
  echo "Starting GPU ${gpu} on port ${port}; log: ${log_file}"
  CUDA_VISIBLE_DEVICES="$gpu" nohup python -m vllm.entrypoints.openai.api_server \
    --model "$MODEL" \
    --host "$HOST" \
    --port "$port" \
    --dtype auto \
    --max-model-len "$MAX_MODEL_LEN" \
    --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION" \
    > "$log_file" 2>&1 &
  echo $! > "$pid_file"
done

for gpu in $(seq 0 $((NUM_GPUS - 1))); do
  port=$((BASE_PORT + gpu))
  log_file="${LOG_DIR}/vllm_gpu${gpu}_port${port}.log"
  echo "Waiting for port ${port}..."
  wait_for_server "$port" "$log_file"
done

echo "All ${NUM_GPUS} vLLM servers are ready."
