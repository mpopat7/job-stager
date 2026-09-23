#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Google sign-in: the client file downloaded from Cloud Console lives outside the repo.
OAUTH_FILE="$HOME/.config/gcp/jobstager-oauth-client.json"
if [ -f "$OAUTH_FILE" ]; then
    export GOOGLE_OAUTH_CLIENT_ID="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["web"]["client_id"])' "$OAUTH_FILE")"
    export GOOGLE_OAUTH_CLIENT_SECRET="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["web"]["client_secret"])' "$OAUTH_FILE")"
fi

if ! lsof -i :8000 | grep -q LISTEN; then
    /opt/homebrew/bin/uv run uvicorn web.server:app --host 127.0.0.1 --port 8000 > "$DIR/server.log" 2>&1 &
    sleep 1
fi

open "http://localhost:8000"
