#!/usr/bin/env bash
# Runs rust-observatory binary with output teed to both terminal and rotating log.
# Logs go to /tmp/claude/observatory/rust-observatory.log (rotated at 10MB, keeps 10 files).
# Only stdout (event data) is logged; stderr (HTTP log lines) goes to terminal only.
# Rotation happens at startup; for mid-session rotation, send SIGUSR1 or restart.
# Usage: ./run-with-tee-logrotator.sh [server args...]
# Example: ./run-with-tee-logrotator.sh tcp --pretty-yaml
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BINARY="${SCRIPT_DIR}/target/release/rust-observatory"
LOG_DIR="/tmp/claude/observatory"
LOG_FILE="${LOG_DIR}/rust-observatory.log"
MAX_SIZE=${LOG_MAX_SIZE:-10485760}  # 10MB default, override with LOG_MAX_SIZE
MAX_COUNT=${LOG_MAX_COUNT:-10}      # 10 files default, override with LOG_MAX_COUNT

rotate_if_needed() {
    [[ ! -f "$LOG_FILE" ]] && return
    local size
    size=$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)
    if (( size > MAX_SIZE )); then
        echo "Rotating $LOG_FILE (${size} bytes > ${MAX_SIZE} limit)..." >&2
        for (( i=MAX_COUNT-1; i>=1; i-- )); do
            [[ -f "${LOG_FILE}.${i}" ]] && mv "${LOG_FILE}.${i}" "${LOG_FILE}.$((i+1))"
        done
        mv "$LOG_FILE" "${LOG_FILE}.1"
        rm -f "${LOG_FILE}.$((MAX_COUNT+1))"
    fi
}

if [[ ! -x "$BINARY" ]]; then
    echo "Error: binary not found at ${BINARY}" >&2
    echo "Build it first: cargo build --release" >&2
    exit 1
fi

mkdir -p "$LOG_DIR"
rotate_if_needed

echo "Logging event data to: $LOG_FILE" >&2
"$BINARY" "$@" | tee -a "$LOG_FILE"
