#!/bin/bash
set -e

echo "Starting trade-pilot scheduler..."
python main.py &
SCHEDULER_PID=$!

echo "Starting API server..."
uvicorn api.server:app --host 0.0.0.0 --port ${PORT:-8000}

# If uvicorn exits, kill the scheduler too
kill $SCHEDULER_PID
