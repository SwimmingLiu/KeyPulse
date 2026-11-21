#!/usr/bin/env bash
# Pin KeyPulse to deterministic, network-free replay inside an eval workspace.
#
# The eval sandbox denies outbound network, so real lookups are impossible.
# This script materialises the recording shipped beside it (recording.json)
# into a *written* replay cache that KeyPulse reads natively under --offline.
set -uo pipefail

WORKSPACE="$PWD"
CACHE_DIR="$WORKSPACE/.kp-cache"
mkdir -p "$CACHE_DIR"

RECORDING="$(dirname "$0")/recording.json"
if [ ! -f "$RECORDING" ]; then
  echo "WARNING: recording.json missing next to the fixture ($RECORDING)" >&2
fi

python3 - "$RECORDING" "$CACHE_DIR" <<'PY'
import json, sys
from pathlib import Path

recording_path, cache_dir = Path(sys.argv[1]), Path(sys.argv[2])
if not recording_path.exists():
    sys.exit(0)
data = json.loads(recording_path.read_text(encoding="utf-8"))
count = 0
for keyword, providers in (data.get("keywords") or {}).items():
    for provider, entry in providers.items():
        payload = {"provider": provider, "keyword": keyword, **entry}
        (cache_dir / f"replay-{provider}-{keyword}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        count += 1
print(f"materialised {count} replay entries")
PY

cat > "$WORKSPACE/.keypulse.json" <<JSON
{
  "offline": true,
  "cache_dir": "$CACHE_DIR",
  "retries": 0,
  "timeout": 5
}
JSON
echo "offline replay cache: $CACHE_DIR"
