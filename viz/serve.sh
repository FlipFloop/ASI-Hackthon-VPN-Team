#!/usr/bin/env bash
# Serve the visualization over HTTP (required — file:// is blocked by CORS).
# Uses serve.py so the live jet-fuel feed (/api/fuel) is available; the page
# falls back to the manual slider if that endpoint is missing.
cd "$(dirname "$0")" || exit 1
PORT="${1:-8765}"
# prefer the data-bundle venv (has anthropic for /api/chat, and fuelfeed for
# /api/fuel); fall back to the system python3 if it isn't present.
PY="../hackathon_data_bundle/.venv/bin/python"
[ -x "$PY" ] || PY=python3
exec "$PY" serve.py "$PORT"
