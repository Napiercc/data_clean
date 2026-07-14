#!/usr/bin/env bash
set -euo pipefail

MODEL="${MODEL:-../models/Qwen3-32B}"
INPUT="${INPUT:-input/post_relevance_filtered.csv}"
RUN_DIR="${RUN_DIR:-output/qwen32b_8gpu_sample}"
WORK_DIR="${WORK_DIR:-${RUN_DIR}/dynamic}"
FINAL_DIR="${FINAL_DIR:-${RUN_DIR}/merged}"
BASE_PORT="${BASE_PORT:-8000}"
WORKERS="${WORKERS:-16}"
TIMEOUT="${TIMEOUT:-90}"
MAX_RETRIES="${MAX_RETRIES:-2}"
MAX_OUTPUT_TOKENS="${MAX_OUTPUT_TOKENS:-1024}"
MAX_POST_CHARS="${MAX_POST_CHARS:-2500}"
SAMPLE_PER_PLATFORM="${SAMPLE_PER_PLATFORM:-100}"
PROGRESS_INTERVAL="${PROGRESS_INTERVAL:-15}"
FAIL_ON_ERRORS="${FAIL_ON_ERRORS:-0}"

mkdir -p "${RUN_DIR}/logs" "$WORK_DIR" "$FINAL_DIR"
LOG_FILE="${RUN_DIR}/logs/dynamic_sample_runner.log"

TOTAL_ROWS="${TOTAL_ROWS:-$(python - "$INPUT" "$SAMPLE_PER_PLATFORM" <<'PY'
import csv
import sys
from collections import Counter

counts = Counter()
with open(sys.argv[1], "r", encoding="utf-8-sig", newline="") as f:
    for row in csv.DictReader(f):
        counts[row.get("platform", "unknown")] += 1
print(sum(min(int(sys.argv[2]), count) for count in counts.values()))
PY
)}"

check_api_server() {
  if ! python - "$BASE_PORT" <<'PY' >/dev/null 2>&1
import sys
import urllib.request

urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/v1/models", timeout=5).read()
PY
  then
    echo "vLLM endpoint on port ${BASE_PORT} is not ready. Start/fix the service before running." >&2
    exit 1
  fi
}

count_row_status() {
  python - "$WORK_DIR/llm_post_relevance_pairs.jsonl" <<'PY'
import json
import sys
from pathlib import Path

latest = {}
path = Path(sys.argv[1])
if path.exists():
    with path.open(encoding="utf-8-sig") as f:
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
print(attempted, attempted - errors, errors)
PY
}

progress_loop() {
  while kill -0 "$RUNNER_PID" >/dev/null 2>&1; do
    read -r attempted_rows success_rows error_rows <<< "$(count_row_status)"
    percent=0
    if [[ "$TOTAL_ROWS" -gt 0 ]]; then
      percent=$((success_rows * 100 / TOTAL_ROWS))
    fi
    echo "$(date '+%Y-%m-%d %H:%M:%S') progress: success ${success_rows}/${TOTAL_ROWS} (${percent}%), attempted: ${attempted_rows}, errors: ${error_rows}, worker pool: ${WORKERS}"
    sleep "$PROGRESS_INTERVAL"
  done
}

check_api_server

resume_sources=()
for shard_dir in "${RUN_DIR}"/shard_*; do
  [[ -d "$shard_dir" ]] && resume_sources+=("$shard_dir")
done
resume_args=()
if [[ "${#resume_sources[@]}" -gt 0 ]]; then
  resume_args=(--resume_from "${resume_sources[@]}")
fi

echo "Starting dynamic sample runner with ${WORKERS} concurrent requests on port ${BASE_PORT}; log: ${LOG_FILE}"
python -u llm_post_filter.py \
  --base_url "http://127.0.0.1:${BASE_PORT}/v1" \
  --model "$MODEL" \
  --input "$INPUT" \
  --output_dir "$WORK_DIR" \
  --sample_per_platform "$SAMPLE_PER_PLATFORM" \
  --workers "$WORKERS" \
  --timeout "$TIMEOUT" \
  --max_retries "$MAX_RETRIES" \
  --max_output_tokens "$MAX_OUTPUT_TOKENS" \
  --max_post_chars "$MAX_POST_CHARS" \
  --resume \
  "${resume_args[@]}" \
  > "$LOG_FILE" 2>&1 &
RUNNER_PID="$!"

progress_loop &
PROGRESS_PID="$!"

runner_status=0
if ! wait "$RUNNER_PID"; then
  runner_status=1
fi
kill "$PROGRESS_PID" >/dev/null 2>&1 || true
wait "$PROGRESS_PID" >/dev/null 2>&1 || true

read -r attempted_rows success_rows error_rows <<< "$(count_row_status)"
percent=0
if [[ "$TOTAL_ROWS" -gt 0 ]]; then
  percent=$((success_rows * 100 / TOTAL_ROWS))
fi
echo "$(date '+%Y-%m-%d %H:%M:%S') final: success ${success_rows}/${TOTAL_ROWS} (${percent}%), attempted: ${attempted_rows}, errors: ${error_rows}"

python llm_post_filter.py --merge_dirs "$WORK_DIR" --output_dir "$FINAL_DIR"
echo "Sample merged output: ${FINAL_DIR}/llm_post_relevance_filtered.csv"

if [[ "$runner_status" -ne 0 ]]; then
  echo "Sample runner failed unexpectedly. Check ${LOG_FILE}" >&2
  exit "$runner_status"
fi
if [[ "$error_rows" -gt 0 ]]; then
  echo "${error_rows} rows remain failed. They are recorded in ${FINAL_DIR}/llm_post_relevance_errors.csv and will retry on the next run." >&2
  if [[ "$FAIL_ON_ERRORS" == "1" ]]; then
    exit 1
  fi
fi
