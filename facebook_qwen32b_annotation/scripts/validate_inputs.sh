#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python}"

INPUT_XLSX="${PACKAGE_ROOT}/input/facebook_comments_comprehensive_final.xlsx"
PROMPT_FILE="${PACKAGE_ROOT}/input/ai_annotation_prompt.md"
SCHEMA_FILE="${PACKAGE_ROOT}/config/annotation_schema.json"
OUTPUT_DIR="${PACKAGE_ROOT}/output/input_validation_v4_three_fields"
LOG_DIR="${OUTPUT_DIR}/logs"

EXPECTED_XLSX_SHA256="a3053901438bc10b8570146117fd91b72d8fee075951126702cccea557e04222"
EXPECTED_PROMPT_SHA256="5385903490949a686d6eb4061e7193cb2835a1655b864db7c4947eea7bbdfbe9"

mkdir -p "${LOG_DIR}" "${OUTPUT_DIR}/final"

check_sha256() {
  local file_path="$1"
  local expected="$2"
  local label="$3"

  if [[ ! -f "${file_path}" ]]; then
    echo "ERROR: missing ${label}: ${file_path}" >&2
    exit 1
  fi

  local actual
  actual="$("${PYTHON_BIN}" -c 'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "${file_path}")"
  if [[ "${actual}" != "${expected}" ]]; then
    echo "ERROR: ${label} SHA-256 mismatch." >&2
    echo "Expected: ${expected}" >&2
    echo "Actual:   ${actual}" >&2
    exit 1
  fi
  echo "${label} SHA-256 verified: ${actual}"
}

check_sha256 "${INPUT_XLSX}" "${EXPECTED_XLSX_SHA256}" "frozen workbook"
check_sha256 "${PROMPT_FILE}" "${EXPECTED_PROMPT_SHA256}" "frozen prompt"

"${PYTHON_BIN}" "${PACKAGE_ROOT}/annotate_facebook_threads.py" \
  --input-xlsx "${INPUT_XLSX}" \
  --prompt-file "${PROMPT_FILE}" \
  --schema-file "${SCHEMA_FILE}" \
  --output-dir "${OUTPUT_DIR}" \
  --output-xlsx "${OUTPUT_DIR}/final/input_validation_only.xlsx" \
  --model "${API_MODEL_NAME:-Qwen3-32B}" \
  --base-urls "${BASE_URLS:-http://127.0.0.1:8000/v1,http://127.0.0.1:8001/v1}" \
  --workers 1 \
  --timeout 180 \
  --network-retries 0 \
  --semantic-retries 0 \
  --max-output-tokens 768 \
  --max-thread-chars 40000 \
  --max-post-chars 8000 \
  --seed 20260720 \
  --disable-thinking \
  --progress-every 100 \
  --validate-only \
  2>&1 | tee "${LOG_DIR}/validate_inputs.log"

echo "Input hashes and local structure validated; no model request was made."
