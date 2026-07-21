#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python}"

RUN_DIR="${PACKAGE_ROOT}/output/qwen32b_8gpu"
LOG_DIR="${RUN_DIR}/logs"
FINAL_DIR="${RUN_DIR}/final"
mkdir -p "${LOG_DIR}" "${FINAL_DIR}"

"${PYTHON_BIN}" "${PACKAGE_ROOT}/annotate_facebook_threads.py" \
  --input-xlsx "${PACKAGE_ROOT}/input/facebook_comments_comprehensive_final.xlsx" \
  --prompt-file "${PACKAGE_ROOT}/input/ai_annotation_prompt.md" \
  --schema-file "${PACKAGE_ROOT}/config/annotation_schema.json" \
  --output-dir "${RUN_DIR}" \
  --output-xlsx "${FINAL_DIR}/facebook_comments_comprehensive_annotated.xlsx" \
  --model "${API_MODEL_NAME:-Qwen3-32B}" \
  --base-urls "${BASE_URLS:-http://127.0.0.1:8000/v1,http://127.0.0.1:8001/v1}" \
  --workers "${WORKERS:-16}" \
  --timeout "${REQUEST_TIMEOUT:-180}" \
  --network-retries "${NETWORK_RETRIES:-5}" \
  --semantic-retries "${SEMANTIC_RETRIES:-2}" \
  --max-output-tokens "${MAX_OUTPUT_TOKENS:-768}" \
  --max-thread-chars "${MAX_THREAD_CHARS:-40000}" \
  --max-post-chars "${MAX_POST_CHARS:-8000}" \
  --seed "${SAMPLE_SEED:-20260720}" \
  --resume \
  --disable-thinking \
  --progress-every "${PROGRESS_EVERY:-50}" \
  --fail-on-errors \
  2>&1 | tee -a "${LOG_DIR}/annotate.log"
