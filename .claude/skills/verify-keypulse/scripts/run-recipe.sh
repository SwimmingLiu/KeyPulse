#!/usr/bin/env bash
# Create an isolated workspace for one verification recipe.
#
#   run-recipe.sh <run-id> <feature-id>
#
# Prints the workspace path on stdout. Everything else it writes goes under
# $TMPDIR/keypulse-verify/<run-id>/<feature-id>/ so a run never touches the
# developer's cache, config, or repo.
set -euo pipefail

RUN_ID="${1:?usage: run-recipe.sh <run-id> <feature-id>}"
FEATURE="${2:?usage: run-recipe.sh <run-id> <feature-id>}"

REPO="$(cd "$(dirname "$0")/../../../.." && pwd)"
WS="${TMPDIR:-/tmp}/keypulse-verify/$RUN_ID/$FEATURE"
mkdir -p "$WS/artifacts/$FEATURE"

cat > "$WS/.keypulse.json" <<JSON
{
  "offline": true,
  "cache_dir": "$WS/.cache",
  "retries": 0,
  "timeout": 5
}
JSON

mkdir -p "$WS/.cache"
# Replay recorded provider values so the recipe exercises KeyPulse logic, not
# upstream availability. The recording is the same one the eval suite uses.
python3 - "$REPO/evals/fixtures/cache.json" "$WS/.cache" <<'PY'
import json, sys
from pathlib import Path
recording, cache = Path(sys.argv[1]), Path(sys.argv[2])
if not recording.exists():
    sys.exit(f"recording not found: {recording}")
data = json.loads(recording.read_text(encoding="utf-8"))
for keyword, providers in (data.get("keywords") or {}).items():
    for provider, entry in providers.items():
        payload = {"provider": provider, "keyword": keyword, **entry}
        (cache / f"replay-{provider}-{keyword}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
PY

echo "$WS"
