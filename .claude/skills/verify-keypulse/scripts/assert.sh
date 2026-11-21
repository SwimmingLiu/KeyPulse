#!/usr/bin/env bash
# Record a command's exit code and output, then fail if the code is unexpected.
#
#   assert.sh <expected-exit> <label> -- <command...>
#
# Writes <label>.cmd.txt, <label>.out.txt and <label>.err.txt into ./artifacts.
set -uo pipefail

EXPECTED="${1:?usage: assert.sh <expected-exit> <label> -- <cmd...>}"
LABEL="${2:?usage: assert.sh <expected-exit> <label> -- <cmd...>}"
shift 2
[ "${1:-}" = "--" ] && shift

OUTDIR="artifacts"
mkdir -p "$OUTDIR"
printf '%s\nexit=%s\n' "$*" "$EXPECTED" > "$OUTDIR/$LABEL.cmd.txt"

"$@" > "$OUTDIR/$LABEL.out.txt" 2> "$OUTDIR/$LABEL.err.txt"
ACTUAL=$?
printf 'exit=%s\n' "$ACTUAL" >> "$OUTDIR/$LABEL.cmd.txt"

if [ "$ACTUAL" -ne "$EXPECTED" ]; then
  echo "FAIL $LABEL: expected exit $EXPECTED, got $ACTUAL" >&2
  sed -n '1,20p' "$OUTDIR/$LABEL.err.txt" >&2
  exit 1
fi
echo "ok   $LABEL (exit $ACTUAL)"
