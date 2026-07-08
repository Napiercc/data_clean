#!/usr/bin/env bash
set -euo pipefail

MODEL="${MODEL:-Qwen/Qwen3-32B-Instruct}"
BASE_URL="${BASE_URL:-http://localhost:8000/v1}"
INPUT="${INPUT:-input/post_relevance_filtered.csv}"
OUTPUT_DIR="${OUTPUT_DIR:-output/qwen32b_sample}"
SAMPLE_PER_PLATFORM="${SAMPLE_PER_PLATFORM:-100}"
WORKERS="${WORKERS:-4}"

python llm_post_filter.py \
  --base_url "$BASE_URL" \
  --model "$MODEL" \
  --input "$INPUT" \
  --output_dir "$OUTPUT_DIR" \
  --sample_per_platform "$SAMPLE_PER_PLATFORM" \
  --workers "$WORKERS" \
  --resume
