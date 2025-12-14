#!/bin/bash

# -------------------------------------------------------------------
# run_continous_sync.sh
# Continuously sync ONE user from $USER_ID
# -------------------------------------------------------------------

# Absolute paths
PROJECT_ROOT="/project"
BACKEND_DIR="$PROJECT_ROOT/backend"
PYTHON_BIN="$BACKEND_DIR/.venv/bin/python"
SYNC_SCRIPT="$BACKEND_DIR/src/sync_service.py"

# Make sure USER_ID is set
if [ -z "$USER_ID" ]; then
    echo "Please set USER_ID environment variable, e.g.:"
    echo "export USER_ID='alice@thalamind.onmicrosoft.com'"
    exit 1
fi

# Time to wait between syncs (in seconds)
SYNC_INTERVAL=60

# Logging function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# Infinite loop to run continuous sync for a single user
while true; do
    log "Starting sync cycle for user: $USER_ID"

    $PYTHON_BIN "$SYNC_SCRIPT" --sync "$USER_ID"
    if [ $? -ne 0 ]; then
        log "Error syncing user $USER_ID"
    fi

    log "Sync cycle complete. Sleeping $SYNC_INTERVAL seconds..."
    sleep $SYNC_INTERVAL
done
