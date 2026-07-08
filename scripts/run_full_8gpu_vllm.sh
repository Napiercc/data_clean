#!/usr/bin/env bash
set -euo pipefail

MODEL="${MODEL:-../models/Qwen3-32B}"
INPUT="${INPUT:-input/post_relevance_filtered.csv}"
RUN_DIR="${RUN_DIR:-output/qwen32b_8gpu}"
BASE_PORT="${BASE_PORT:-8000}"
NUM_SHARDS="${NUM_SHARDS:-8}"
WORKERS_PER_SHARD="${WORKERS_PER_SHARD:-4}"
PROGRESS_INTERVAL="${PROGRESS_INTERVAL:-30}"

mkdir -p "${RUN_DIR}/logs"

TOTAL_ROWS="${TOTAL_ROWS:-$(python - "$INPUT" <<'PY'
import csv
import sys

with open(sys.argv[1], "r", encoding="utf-8-sig", newline="") as f:
    print(sum(1 for _ in csv.DictReader(f)))
PY
)}"

count_done_rows() {
  local total=0
  local file
  local lines
  for file in "${RUN_DIR}"/shard_*/llm_post_relevance_pairs.jsonl; do
    [[ -f "$file" ]] || continue
    lines="$(wc -l < "$file" | tr -d ' ')"
    total=$((total + lines))
  done
  echo "$total"
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
  local done_rows
  local running
  local percent
  while true; do
    done_rows="$(count_done_rows)"
    running="$(running_jobs)"
    if [[ "$TOTAL_ROWS" -gt 0 ]]; then
      percent=$((done_rows * 100 / TOTAL_ROWS))
    else
      percent=0
    fi
    echo "$(date '+%Y-%m-%d %H:%M:%S') progress: ${done_rows}/${TOTAL_ROWS} (${percent}%), running shards: ${running}/${NUM_SHARDS}"
    [[ "$running" -eq 0 ]] && break
    sleep "$PROGRESS_INTERVAL"
  done
}

pids=()
for shard in $(seq 0 $((NUM_SHARDS - 1))); do
  port=$((BASE_PORT + shard))
  shard_dir="${RUN_DIR}/shard_${shard}"
  log_file="${RUN_DIR}/logs/shard_${shard}.log"
  echo "Starting shard ${shard}/${NUM_SHARDS} on port ${port}; log: ${log_file}"
  python -u llm_post_filter.py \
    --base_url "http://127.0.0.1:${port}/v1" \
    --model "$MODEL" \
    --input "$INPUT" \
    --output_dir "$shard_dir" \
    --num_shards "$NUM_SHARDS" \
    --shard_index "$shard" \
    --workers "$WORKERS_PER_SHARD" \
    --resume \
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

done_rows="$(count_done_rows)"
if [[ "$TOTAL_ROWS" -gt 0 ]]; then
  final_percent=$((done_rows * 100 / TOTAL_ROWS))
else
  final_percent=0
fi
echo "$(date '+%Y-%m-%d %H:%M:%S') progress: ${done_rows}/${TOTAL_ROWS} (${final_percent}%), shard jobs finished"

if [[ "$status" -ne 0 ]]; then
  echo "At least one shard failed. Check ${RUN_DIR}/logs/shard_*.log" >&2
  exit "$status"
fi

python llm_post_filter.py \
  --merge_dirs "${RUN_DIR}"/shard_* \
  --output_dir "${RUN_DIR}/merged"

echo "Final merged output: ${RUN_DIR}/merged/llm_post_relevance_filtered.csv"
