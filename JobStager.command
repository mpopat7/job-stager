#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

if ! lsof -i :8000 | grep -q LISTEN; then
    /opt/homebrew/bin/uv run uvicorn web.server:app --host 127.0.0.1 --port 8000 > "$DIR/server.log" 2>&1 &
    sleep 1
fi

open "http://localhost:8000"
