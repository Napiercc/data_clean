#!/usr/bin/env bash
set -euo pipefail

PID_DIR="${PID_DIR:-run/vllm_pids}"

if [[ ! -d "$PID_DIR" ]]; then
  echo "No PID directory found: ${PID_DIR}"
  exit 0
fi

for pid_file in "${PID_DIR}"/*.pid; do
  [[ -e "$pid_file" ]] || continue
  pid="$(cat "$pid_file")"
  if kill -0 "$pid" >/dev/null 2>&1; then
    echo "Stopping PID ${pid} from ${pid_file}"
    kill "$pid"
  fi
  rm -f "$pid_file"
done

echo "Stop signal sent to vLLM servers."
