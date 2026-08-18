#!/usr/bin/env bash
set -u

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

pids=()
jobs_enabled="${JOBS_ENABLED:-false}"
collection_enabled="${JOBS_WEB_COLLECTION_ENABLED:-false}"

shutdown() {
  trap - EXIT INT TERM
  if ((${#pids[@]})); then
    kill -TERM "${pids[@]}" 2>/dev/null || true
    wait "${pids[@]}" 2>/dev/null || true
  fi
}

trap shutdown EXIT INT TERM

if [[ "${jobs_enabled,,}" =~ ^(1|true|yes|on)$ ]]; then
  ./.venv/bin/python -m tools.jobs initialize || exit $?
fi

./.venv/bin/python -m uvicorn portal.main:app --reload --host 127.0.0.1 --port 8000 &
pids+=("$!")

if [[ "${jobs_enabled,,}" =~ ^(1|true|yes|on)$ && "${collection_enabled,,}" =~ ^(1|true|yes|on)$ ]]; then
  ./.venv/bin/python -m tools.jobs worker &
  pids+=("$!")
  printf '%s\n' 'Jobs worker started.'
fi

wait -n "${pids[@]}"
status=$?
shutdown
exit "$status"
