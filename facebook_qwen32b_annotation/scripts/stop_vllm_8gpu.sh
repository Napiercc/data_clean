#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PID_DIR="${PACKAGE_ROOT}/run"
STOP_TIMEOUT="${STOP_TIMEOUT:-30}"

stop_instance() {
  local port="$1"
  local pid_file="${PID_DIR}/vllm-${port}.pid"

  if [[ ! -f "${pid_file}" ]]; then
    echo "Port ${port}: no PID file; skipping."
    return 0
  fi

  local pid
  pid="$(<"${pid_file}")"
  if [[ ! "${pid}" =~ ^[0-9]+$ ]]; then
    echo "WARNING: ${pid_file} does not contain a valid PID; no process was terminated." >&2
    return 1
  fi

  if ! kill -0 "${pid}" 2>/dev/null; then
    echo "Port ${port}: process ${pid} no longer exists."
    rm -f -- "${pid_file}"
    return 0
  fi

  # Prevent a stale, reused PID from terminating an unrelated server process.
  local cmdline_file="/proc/${pid}/cmdline"
  local cmdline=""
  if [[ -r "${cmdline_file}" ]]; then
    cmdline="$(tr '\0' ' ' <"${cmdline_file}")"
  fi
  if [[ "${cmdline}" != *"vllm.entrypoints.openai.api_server"* || "${cmdline}" != *"--port ${port}"* ]]; then
    echo "WARNING: PID ${pid} does not match this package's vLLM command for port ${port}; refusing to terminate it." >&2
    echo "Inspect ${pid_file} and /proc/${pid}/cmdline manually." >&2
    return 1
  fi

  echo "Port ${port}: sending TERM to process ${pid}."
  kill -TERM "${pid}"
  local deadline=$((SECONDS + STOP_TIMEOUT))
  while kill -0 "${pid}" 2>/dev/null && (( SECONDS < deadline )); do
    sleep 1
  done

  if kill -0 "${pid}" 2>/dev/null; then
    echo "Port ${port}: process did not exit within ${STOP_TIMEOUT} seconds; sending KILL." >&2
    kill -KILL "${pid}"
  fi
  rm -f -- "${pid_file}"
}

stop_instance 8000
stop_instance 8001
echo "Stopped the vLLM services recorded by this package."
