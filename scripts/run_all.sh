#!/usr/bin/env bash
set -euo pipefail
# Start LLM server
python llm_server/llm_server.py &
LLM_PID=$!
sleep 1
# Start App server
python server/app_server.py &
APP_PID=$!
sleep 1
# Run a demo client flow
python client/client.py --demo
# Wait a bit then kill servers
kill $APP_PID $LLM_PID 2>/dev/null || true
