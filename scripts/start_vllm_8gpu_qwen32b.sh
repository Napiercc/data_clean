#!/usr/bin/env bash
set -euo pipefail

MODEL="${MODEL:-../models/Qwen3-32B}"
HOST="${HOST:-0.0.0.0}"
BASE_PORT="${BASE_PORT:-8000}"
GPU_GROUPS="${GPU_GROUPS:-0,1,2,3 4,5,6,7}"
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

read -r -a gpu_groups <<< "$GPU_GROUPS"

for group_index in "${!gpu_groups[@]}"; do
  gpu_group="${gpu_groups[$group_index]}"
  IFS=',' read -r -a group_gpus <<< "$gpu_group"
  tensor_parallel_size="${#group_gpus[@]}"
  port=$((BASE_PORT + group_index))
  log_file="${LOG_DIR}/vllm_group${group_index}_tp${tensor_parallel_size}_port${port}.log"
  pid_file="${PID_DIR}/vllm_group${group_index}_tp${tensor_parallel_size}_port${port}.pid"
  echo "Starting GPU group ${group_index} (${gpu_group}) with TP=${tensor_parallel_size} on port ${port}; log: ${log_file}"
  CUDA_VISIBLE_DEVICES="$gpu_group" nohup python -m vllm.entrypoints.openai.api_server \
    --model "$MODEL" \
    --host "$HOST" \
    --port "$port" \
    --dtype auto \
    --tensor-parallel-size "$tensor_parallel_size" \
    --max-model-len "$MAX_MODEL_LEN" \
    --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION" \
    > "$log_file" 2>&1 &
  echo $! > "$pid_file"
done

for group_index in "${!gpu_groups[@]}"; do
  gpu_group="${gpu_groups[$group_index]}"
  IFS=',' read -r -a group_gpus <<< "$gpu_group"
  tensor_parallel_size="${#group_gpus[@]}"
  port=$((BASE_PORT + group_index))
  log_file="${LOG_DIR}/vllm_group${group_index}_tp${tensor_parallel_size}_port${port}.log"
  echo "Waiting for port ${port}..."
  wait_for_server "$port" "$log_file"
done

echo "All ${#gpu_groups[@]} tensor-parallel vLLM servers are ready."
