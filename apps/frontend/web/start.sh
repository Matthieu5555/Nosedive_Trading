#!/usr/bin/env bash
#
# start.sh — one command to run the operator web frontend, every time.
#
#   ./start.sh            start the stack (backend + frontend; no-op if already healthy)
#   ./start.sh restart    kill EVERYTHING (backend + frontend) and start fresh
#   ./start.sh stop       kill everything
#   ./start.sh status     show what's running, on which port, against which backend
#   ./start.sh logs       tail the dev-server log
#
# This drives the WHOLE operator stack: the FastAPI BFF (uvicorn) and the Vite
# frontend that proxies to it. `restart` reclaims both ports and starts both
# fresh, so one command picks up new backend code (e.g. per-side vol surfaces).
#
# Why this script exists: starting it by hand is fiddly for three reasons,
# and this encodes the fix for all three:
#   1. The shared backend on :8000 is usually dead, so the Vite proxy 500s on /api/*
#      unless you point it elsewhere with BFF_TARGET.
#   2. *Which* backend is live moves around (:8090, :8001, ...). This auto-probes
#      and picks the first one that actually serves /api/indices.
#   3. Stale dev servers squat the port. `restart` reclaims it cleanly.
#
# Overrides (env vars):
#   WEB_PORT=5173        port for the dev server (default 5173)
#   BACKEND_PORT=8090    port for the managed BFF (default 8090)
#   BACKEND_APP=…        uvicorn app path (default algotrading.frontend.app:app)
#   BFF_TARGET=http://…  point at an EXTERNAL backend instead. When set, this
#                        script proxies to it and does NOT start/stop a local one.
#
set -euo pipefail

WEB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$WEB_DIR/../../.." && pwd)"
PORT="${WEB_PORT:-5173}"
PIDFILE="/tmp/algotrading-web-${PORT}.pid"
LOGFILE="/tmp/algotrading-web-${PORT}.log"

# The operator BFF (FastAPI/uvicorn) this front talks to. `restart` owns it too, so one
# command brings the whole stack up fresh. Set BFF_TARGET to point at an externally-managed
# backend instead — then this script won't start or stop a local one.
BACKEND_PORT="${BACKEND_PORT:-8090}"
BACKEND_APP="${BACKEND_APP:-algotrading.frontend.app:app}"
BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}"
BACKEND_LOG="/tmp/algotrading-bff-${BACKEND_PORT}.log"
PY="$REPO_ROOT/.venv/bin/python"
# Manage a local backend unless the operator forced an external one via BFF_TARGET.
MANAGE_BACKEND=1; [ -n "${BFF_TARGET:-}" ] && MANAGE_BACKEND=0

# Backends to try, in order. A forced BFF_TARGET wins; otherwise probe the usual suspects.
CANDIDATES=("${BFF_TARGET:-}" "http://127.0.0.1:8090" "http://127.0.0.1:8001" "http://127.0.0.1:8000")

c_grn=$'\033[32m'; c_red=$'\033[31m'; c_yel=$'\033[33m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
say()  { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$c_grn" "$c_off" "$*"; }
warn() { printf '%s!%s %s\n' "$c_yel" "$c_off" "$*"; }
die()  { printf '%s✗ %s%s\n' "$c_red" "$*" "$c_off" >&2; exit 1; }

# A backend is "live" if it answers /api/indices with a 2xx. /api/indices degrades to an
# empty 200 on config drift, so a 200 means the proxy path genuinely works end to end.
backend_live() { curl -fsS -o /dev/null --max-time 3 "$1/api/indices" 2>/dev/null; }

# Report whether the backend's assistant is wired to OpenRouter, and which model it
# loaded from .env. This is a cheap no-LLM readiness probe (GET /api/assistant/health):
# 200 + {configured:true, model:…} when the key is present, 503 when it is not. The BFF
# self-loads the repo-root .env, so this reflects the operator's OPENROUTER_API_KEY /
# ASSISTANT_MODEL without this script needing to touch any secret.
report_assistant() {
  local body model
  body="$(curl -fsS --max-time 3 "$1/api/assistant/health" 2>/dev/null)" || {
    warn "assistant: not configured (no OpenRouter key) - banner will show 'unavailable'. Set OPENROUTER_API_KEY in .env and restart the backend."
    return 0
  }
  model="$(printf '%s' "$body" | grep -oE '"model"[ ]*:[ ]*"[^"]*"' | head -1 | sed -E 's/.*"model"[ ]*:[ ]*"([^"]*)".*/\1/')"
  ok "assistant: configured (model ${model:-unknown})"
}

pick_backend() {
  for b in "${CANDIDATES[@]}"; do
    [ -z "$b" ] && continue
    if backend_live "$b"; then echo "$b"; return 0; fi
  done
  return 1
}

# PID listening on $PORT, if any (one per line).
# Note: grep exits 1 when the port is free; `|| true` keeps that from tripping `set -e`/pipefail
# inside command substitutions (which is how this gets called).
port_pids() { ss -ltnpH "sport = :$PORT" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true; }
port_busy() { [ -n "$(port_pids)" ]; }

# Is the server we manage up and serving the app shell?
web_up() { curl -fsS -o /dev/null --max-time 3 "http://127.0.0.1:$PORT/" 2>/dev/null; }

free_port() {
  local pids; pids="$(port_pids)"
  [ -z "$pids" ] && return 0
  warn "port $PORT held by pid(s): $pids — stopping them"
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do port_busy || return 0; sleep 0.3; done
  pids="$(port_pids)"; [ -n "$pids" ] && { warn "force-killing $pids"; kill -9 $pids 2>/dev/null || true; }
  sleep 0.3
}

# --- backend (BFF) lifecycle -------------------------------------------------
backend_pids() { ss -ltnpH "sport = :$BACKEND_PORT" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true; }
backend_busy() { [ -n "$(backend_pids)" ]; }
# Live = answers the proxy probe path with a 2xx, the same bar the front holds the BFF to.
backend_up()   { backend_live "$BACKEND_URL"; }

free_backend_port() {
  local pids; pids="$(backend_pids)"
  [ -z "$pids" ] && return 0
  warn "backend port $BACKEND_PORT held by pid(s): $pids — stopping them"
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do backend_busy || return 0; sleep 0.3; done
  pids="$(backend_pids)"; [ -n "$pids" ] && { warn "force-killing $pids"; kill -9 $pids 2>/dev/null || true; }
  sleep 0.3
}

start_backend() {
  [ "$MANAGE_BACKEND" = 1 ] || { ok "backend: external ${BFF_TARGET} (not managed)"; return 0; }
  if backend_up; then ok "backend already healthy: $BACKEND_URL"; return 0; fi
  free_backend_port
  [ -x "$PY" ] || die "no venv python at $PY — create the .venv first."
  : > "$BACKEND_LOG"
  ( cd "$REPO_ROOT" && exec "$PY" -m uvicorn "$BACKEND_APP" --host 127.0.0.1 --port "$BACKEND_PORT" ) \
    >>"$BACKEND_LOG" 2>&1 &
  for _ in $(seq 1 80); do backend_up && break; sleep 0.25; done
  if backend_up; then
    ok "backend UP → $BACKEND_URL"
  else
    warn "backend did not come up — last lines of $BACKEND_LOG:"; tail -n 20 "$BACKEND_LOG" >&2
    die "backend startup failed"
  fi
}

stop_backend() {
  [ "$MANAGE_BACKEND" = 1 ] || return 0
  free_backend_port
  ok "backend stopped (port $BACKEND_PORT free)"
}

do_status() {
  if [ "$MANAGE_BACKEND" = 1 ]; then
    if backend_up; then
      ok "backend UP on $BACKEND_URL  (pid $(backend_pids | tr '\n' ' '))"
    elif backend_busy; then
      warn "something on :$BACKEND_PORT (pid $(backend_pids | tr '\n' ' ')) but /api/* isn't answering"
    else
      say "${c_dim}backend not running on :$BACKEND_PORT${c_off}"
    fi
  else
    say "${c_dim}backend: external ${BFF_TARGET} (not managed)${c_off}"
  fi

  if web_up; then
    ok "frontend UP on http://127.0.0.1:$PORT  (pid $(port_pids | tr '\n' ' '))"
    if curl -fsS -o /dev/null --max-time 3 "http://127.0.0.1:$PORT/api/indices" 2>/dev/null; then
      ok "API proxy reaching a live backend"
      report_assistant "http://127.0.0.1:$PORT"
    else
      warn "app shell serves but /api/* is failing — backend down or proxy misconfigured (restart to re-probe)"
    fi
  elif port_busy; then
    warn "something on :$PORT (pid $(port_pids | tr '\n' ' ')) but it isn't serving the app"
  else
    say "${c_dim}frontend not running on :$PORT${c_off}"
  fi
}

do_stop() {
  free_port
  rm -f "$PIDFILE"
  ok "frontend stopped (port $PORT free)"
  stop_backend
}

do_start() {
  # Bring the BFF up first so the frontend's proxy probe finds it.
  start_backend
  if web_up && curl -fsS -o /dev/null --max-time 3 "http://127.0.0.1:$PORT/api/indices" 2>/dev/null; then
    ok "already running & healthy: http://127.0.0.1:$PORT"
    return 0
  fi
  free_port

  local backend
  backend="$(pick_backend)" || die "no live backend found. Tried: ${CANDIDATES[*]/#/ }
    Start a backend (uvicorn) first, or set BFF_TARGET=http://host:port and re-run."
  ok "backend: $backend"

  : > "$LOGFILE"
  ( cd "$WEB_DIR" && BFF_TARGET="$backend" exec npm run dev -- --port "$PORT" --strictPort ) \
    >>"$LOGFILE" 2>&1 &
  echo $! > "$PIDFILE"

  for _ in $(seq 1 40); do
    web_up && break
    sleep 0.25
  done
  if web_up; then
    ok "frontend UP → http://127.0.0.1:$PORT"
    if curl -fsS -o /dev/null --max-time 3 "http://127.0.0.1:$PORT/api/indices" 2>/dev/null; then
      ok "API proxy → $backend (verified)"
      report_assistant "http://127.0.0.1:$PORT"
    else
      warn "shell up but /api/* not answering yet — check '$0 logs'"
    fi
    say "${c_dim}logs: $0 logs   |   stop: $0 stop${c_off}"
  else
    warn "did not come up — last lines of $LOGFILE:"; tail -n 20 "$LOGFILE" >&2; die "startup failed"
  fi
}

case "${1:-start}" in
  start)   do_start ;;
  restart) do_stop; do_start ;;
  stop)    do_stop ;;
  status)  do_status ;;
  logs)    exec tail -n 100 -f "$LOGFILE" ;;
  *) die "usage: $0 {start|restart|stop|status|logs}" ;;
esac
