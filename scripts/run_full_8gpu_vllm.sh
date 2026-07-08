#!/usr/bin/env bash
set -euo pipefail

MODEL="${MODEL:-Qwen/Qwen3-32B-Instruct}"
INPUT="${INPUT:-input/post_relevance_filtered.csv}"
RUN_DIR="${RUN_DIR:-output/qwen32b_8gpu}"
BASE_PORT="${BASE_PORT:-8000}"
NUM_SHARDS="${NUM_SHARDS:-8}"
WORKERS_PER_SHARD="${WORKERS_PER_SHARD:-4}"

mkdir -p "${RUN_DIR}/logs"

pids=()
for shard in $(seq 0 $((NUM_SHARDS - 1))); do
  port=$((BASE_PORT + shard))
  shard_dir="${RUN_DIR}/shard_${shard}"
  log_file="${RUN_DIR}/logs/shard_${shard}.log"
  echo "Starting shard ${shard}/${NUM_SHARDS} on port ${port}; log: ${log_file}"
  python llm_post_filter.py \
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

status=0
for pid in "${pids[@]}"; do
  if ! wait "$pid"; then
    status=1
  fi
done

if [[ "$status" -ne 0 ]]; then
  echo "At least one shard failed. Check ${RUN_DIR}/logs/shard_*.log" >&2
  exit "$status"
fi

python llm_post_filter.py \
  --merge_dirs "${RUN_DIR}"/shard_* \
  --output_dir "${RUN_DIR}/merged"

echo "Final merged output: ${RUN_DIR}/merged/llm_post_relevance_filtered.csv"
