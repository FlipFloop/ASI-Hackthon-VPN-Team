#!/usr/bin/env bash
# Serve the visualization over HTTP (required — file:// is blocked by CORS).
cd "$(dirname "$0")/public" || exit 1
PORT="${1:-8765}"
echo "Serving on http://localhost:${PORT}/index.html  (Ctrl-C to stop)"
exec python3 -m http.server "$PORT"
