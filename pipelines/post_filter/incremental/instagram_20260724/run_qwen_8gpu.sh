#!/usr/bin/env bash
set -euo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PIPELINE_DIR"

INPUT="${INPUT:-incremental/instagram_20260724/03_qwen_input/instagram_incremental_qwen_input.csv}"
RUN_DIR="${RUN_DIR:-incremental/instagram_20260724/04_qwen_output}"

INPUT="$INPUT" RUN_DIR="$RUN_DIR" bash scripts/run_full_8gpu_vllm.sh
