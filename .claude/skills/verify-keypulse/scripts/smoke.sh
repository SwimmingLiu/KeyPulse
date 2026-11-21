#!/usr/bin/env bash
# Prove the generated verification skill works: doctor + one full recipe.
#
#   smoke.sh [run-id]
#
# Exits 0 only when every assertion passes and the evidence survives cleanup.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../../.." && pwd)"
KP="$REPO/bin/keypulse"
RUN_ID="${1:-kp-smoke-$(date +%s)}"
FAILED=0

step() { printf '\n== %s ==\n' "$*"; }

step "launch"
python3 -m compileall -q "$REPO/src" "$REPO/bin/keypulse" || { echo "FAIL compile"; exit 1; }
"$KP" --version || { echo "FAIL version"; exit 1; }

step "doctor"
"$KP" doctor | tail -3
[ "${PIPESTATUS[0]}" -eq 0 ] || { echo "FAIL doctor"; exit 1; }

step "recipe: scan-ranking"
WS=$("$HERE/run-recipe.sh" "$RUN_ID" scan-ranking) || { echo "FAIL workspace"; exit 1; }
cd "$WS" || exit 1
"$HERE/assert.sh" 0 scan-table  -- "$KP" scan -k "人工智能" "量子计算" -r CN --top 2 || FAILED=1
"$HERE/assert.sh" 0 scan-json   -- "$KP" scan -k "人工智能" "量子计算" -r CN --format json || FAILED=1
"$HERE/assert.sh" 0 top-leader  -- "$KP" top  -k "人工智能" "量子计算" -r CN --top 1 || FAILED=1
"$HERE/assert.sh" 2 no-keywords -- "$KP" scan || FAILED=1

step "assert ranking content"
python3 - artifacts/scan-json.out.txt <<'PY' || FAILED=1
import json, sys
doc = json.load(open(sys.argv[1]))
assert doc["mode"] in {"index", "fallback"}, doc["mode"]
ranked = doc["ranked"]
assert ranked[0]["keyword"] == "人工智能", ranked[0]["keyword"]
assert ranked[0]["score"] > ranked[1]["score"], "winner must outrank runner-up"
# Assert on what the recording guarantees, not on which transport answered:
# with no opencli on the host, baidu_index is not registered at all, so naming
# it here would make the skill depend on the host having a browser bridge.
assert ranked[0]["raw"], "winner must carry raw source values"
assert ranked[0]["confidence"] >= 0.5, ranked[0]["confidence"]
print("ok   ranking content")
PY

step "recipe: scenario-to-seeds"
WS2=$("$HERE/run-recipe.sh" "$RUN_ID" scenario-to-seeds) || { echo "FAIL workspace"; exit 1; }
cd "$WS2" || exit 1
"$HERE/assert.sh" 0 seeds-zh -- "$KP" seeds -s "我想做一款面向程序员的 AI 编程笔记软件，支持代码片段管理和知识库检索" || FAILED=1
grep -q "编程笔记软件" artifacts/seeds-zh.out.txt || { echo "FAIL: whole phrase missing"; FAILED=1; }
if grep -qE '^\s*[0-9]+\.\s*(款面|做一|向程)' artifacts/seeds-zh.out.txt; then
  echo "FAIL: fragment leaked into seeds"; FAILED=1
fi
echo "ok   seeds content"

step "recipe: provider-honesty"
WS3=$("$HERE/run-recipe.sh" "$RUN_ID" provider-honesty) || { echo "FAIL workspace"; exit 1; }
cd "$WS3" || exit 1
"$HERE/assert.sh" 0 providers -- "$KP" providers -r CN || FAILED=1
"$HERE/assert.sh" 0 doctor    -- "$KP" doctor || FAILED=1
"$HERE/assert.sh" 0 doctor-nobridge -- env KEYPULSE_BROWSER=off "$KP" doctor || FAILED=1

step "recipe: doctor failure path"
BROKEN=$("$HERE/run-recipe.sh" "$RUN_ID" doctor-broken) || { echo "FAIL workspace"; exit 1; }
cd "$BROKEN" || exit 1
python3 - "$BROKEN/.keypulse.json" <<'PYJSON'
import json, sys
path = sys.argv[1]
cfg = json.loads(open(path).read())
cfg["cache_dir"] = "/proc/nope/keypulse"
open(path, "w").write(json.dumps(cfg))
PYJSON
"$HERE/assert.sh" 1 doctor-broken -- "$KP" doctor || FAILED=1
grep -q "cache_dir" artifacts/doctor-broken.out.txt || { echo "FAIL: doctor did not name cache_dir"; FAILED=1; }
echo "ok   doctor fails naming cache_dir"

step "cleanup keeps evidence"
cd "$WS" || exit 1
[ -f artifacts/scan-json.out.txt ] || { echo "FAIL: no proof before cleanup"; FAILED=1; }
rm -rf "/tmp/keypulse-proof-$RUN_ID"
mkdir -p "/tmp/keypulse-proof-$RUN_ID"
cp -R artifacts/. "/tmp/keypulse-proof-$RUN_ID/"
"$HERE/cleanup.sh" "$RUN_ID" >/dev/null
[ -d "$WS3" ] && { echo "FAIL: scratch dir survived cleanup"; FAILED=1; }
[ -f "/tmp/keypulse-proof-$RUN_ID/scan-json.out.txt" ] || { echo "FAIL: proof lost"; FAILED=1; }
echo "ok   cleanup left proof at /tmp/keypulse-proof-$RUN_ID"

step "result"
if [ "$FAILED" -ne 0 ]; then echo "SMOKE FAILED"; exit 1; fi
echo "SMOKE PASSED (run $RUN_ID)"
