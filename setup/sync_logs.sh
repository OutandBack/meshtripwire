#!/bin/bash
# Sync detection logs to cloud storage via rclone.
# One-time setup: rclone config   (create a remote, e.g. "gdrive")
# Cron example:   */30 * * * * /home/pi/meshtripwire/setup/sync_logs.sh gdrive:meshtripwire
set -euo pipefail

REMOTE="${1:?usage: sync_logs.sh <rclone-remote:path>}"
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
LOGS_DIR="$(dirname "$SCRIPT_DIR")/logs"

# .backup gives a consistent snapshot even while the monitor is writing
sqlite3 "$LOGS_DIR/detections.db" ".backup '$LOGS_DIR/detections-backup.db'"
rclone copy "$LOGS_DIR" "$REMOTE"
echo "Synced $LOGS_DIR to $REMOTE"
