#!/usr/bin/env bash
# Serve the visualization over HTTP (required — file:// is blocked by CORS).
# Uses serve.py so the live jet-fuel feed (/api/fuel) is available; the page
# falls back to the manual slider if that endpoint is missing.
cd "$(dirname "$0")" || exit 1
PORT="${1:-8765}"
exec python3 serve.py "$PORT"
