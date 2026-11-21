#!/usr/bin/env bash
# Remove scratch workspaces for a run id. Never touches artifacts directories
# that live outside the scratch tree, and never kills processes it did not start.
#
#   cleanup.sh [run-id]
set -uo pipefail
RUN_ID="${1:-}"
BASE="${TMPDIR:-/tmp}/keypulse-verify"
if [ -z "$RUN_ID" ]; then
  echo "usage: cleanup.sh <run-id>   (available:)"
  ls "$BASE" 2>/dev/null || true
  exit 2
fi
TARGET="$BASE/$RUN_ID"
case "$TARGET" in
  "$BASE"/*) rm -rf "$TARGET" && echo "removed $TARGET" ;;
  *) echo "refusing to remove $TARGET" >&2; exit 1 ;;
esac
