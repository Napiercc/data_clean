#!/usr/bin/env bash
set -euo pipefail

MODEL="${MODEL:-../models/Qwen3-32B}"
INPUT="${INPUT:-input/post_relevance_filtered.csv}"
RUN_DIR="${RUN_DIR:-output/qwen32b_8gpu_sample}"
BASE_PORT="${BASE_PORT:-8000}"
GPU_GROUPS="${GPU_GROUPS:-4,5,6,7}"
NUM_SHARDS="${NUM_SHARDS:-8}"
read -r -a endpoint_groups <<< "$GPU_GROUPS"
NUM_ENDPOINTS="${NUM_ENDPOINTS:-${#endpoint_groups[@]}}"
WORKERS_PER_SHARD="${WORKERS_PER_SHARD:-1}"
SAMPLE_PER_PLATFORM="${SAMPLE_PER_PLATFORM:-100}"
PROGRESS_INTERVAL="${PROGRESS_INTERVAL:-30}"

mkdir -p "${RUN_DIR}/logs"

TOTAL_ROWS="${TOTAL_ROWS:-$(python - "$INPUT" "$SAMPLE_PER_PLATFORM" <<'PY'
import csv
import sys
from collections import Counter

sample_per_platform = int(sys.argv[2])
counts = Counter()
with open(sys.argv[1], "r", encoding="utf-8-sig", newline="") as f:
    for row in csv.DictReader(f):
        counts[row.get("platform", "unknown")] += 1
print(sum(min(sample_per_platform, count) for count in counts.values()))
PY
)}"

count_row_status() {
  python - "$RUN_DIR" <<'PY'
import glob
import json
import sys

latest = {}
for path in glob.glob(f"{sys.argv[1]}/shard_*/llm_post_relevance_pairs.jsonl"):
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = str(row.get("llm_row_key") or "")
            if key:
                latest[key] = row

attempted = len(latest)
errors = sum(1 for row in latest.values() if str(row.get("llm_error", "")).strip())
success = attempted - errors
print(attempted, success, errors)
PY
}

check_api_servers() {
  local endpoint
  local port
  for endpoint in $(seq 0 $((NUM_ENDPOINTS - 1))); do
    port=$((BASE_PORT + endpoint))
    if ! python - "$port" <<'PY' >/dev/null 2>&1
import sys
import urllib.request

port = sys.argv[1]
urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=5).read()
PY
    then
      echo "vLLM endpoint on port ${port} is not ready. Start/fix services before running." >&2
      exit 1
    fi
  done
}

running_jobs() {
  local running=0
  local pid
  for pid in "${pids[@]}"; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      running=$((running + 1))
    fi
  done
  echo "$running"
}

progress_loop() {
  local attempted_rows
  local success_rows
  local error_rows
  local running
  local percent
  local counts
  while true; do
    counts="$(count_row_status)"
    read -r attempted_rows success_rows error_rows <<< "$counts"
    running="$(running_jobs)"
    if [[ "$TOTAL_ROWS" -gt 0 ]]; then
      percent=$((success_rows * 100 / TOTAL_ROWS))
    else
      percent=0
    fi
    echo "$(date '+%Y-%m-%d %H:%M:%S') progress: success ${success_rows}/${TOTAL_ROWS} (${percent}%), attempted: ${attempted_rows}, errors: ${error_rows}, running shards: ${running}/${NUM_SHARDS}"
    [[ "$running" -eq 0 ]] && break
    sleep "$PROGRESS_INTERVAL"
  done
}

check_api_servers

pids=()
for shard in $(seq 0 $((NUM_SHARDS - 1))); do
  endpoint=$((shard % NUM_ENDPOINTS))
  port=$((BASE_PORT + endpoint))
  shard_dir="${RUN_DIR}/shard_${shard}"
  log_file="${RUN_DIR}/logs/shard_${shard}.log"
  echo "Starting sample shard ${shard}/${NUM_SHARDS} on endpoint ${endpoint}/${NUM_ENDPOINTS} port ${port}; log: ${log_file}"
  python -u llm_post_filter.py \
    --base_url "http://127.0.0.1:${port}/v1" \
    --model "$MODEL" \
    --input "$INPUT" \
    --output_dir "$shard_dir" \
    --sample_per_platform "$SAMPLE_PER_PLATFORM" \
    --num_shards "$NUM_SHARDS" \
    --shard_index "$shard" \
    --workers "$WORKERS_PER_SHARD" \
    --resume \
    --fail_on_errors \
    > "$log_file" 2>&1 &
  pids+=("$!")
done

progress_loop &
progress_pid="$!"

status=0
for pid in "${pids[@]}"; do
  if ! wait "$pid"; then
    status=1
  fi
done

kill "$progress_pid" >/dev/null 2>&1 || true
wait "$progress_pid" >/dev/null 2>&1 || true

counts="$(count_row_status)"
read -r attempted_rows success_rows error_rows <<< "$counts"
if [[ "$TOTAL_ROWS" -gt 0 ]]; then
  final_percent=$((success_rows * 100 / TOTAL_ROWS))
else
  final_percent=0
fi
echo "$(date '+%Y-%m-%d %H:%M:%S') progress: success ${success_rows}/${TOTAL_ROWS} (${final_percent}%), attempted: ${attempted_rows}, errors: ${error_rows}, sample shard jobs finished"

if [[ "$status" -ne 0 ]]; then
  echo "At least one sample shard failed. Check ${RUN_DIR}/logs/shard_*.log" >&2
  exit "$status"
fi

python llm_post_filter.py \
  --merge_dirs "${RUN_DIR}"/shard_* \
  --output_dir "${RUN_DIR}/merged" \
  --fail_on_errors

echo "Sample merged output: ${RUN_DIR}/merged/llm_post_relevance_filtered.csv"
