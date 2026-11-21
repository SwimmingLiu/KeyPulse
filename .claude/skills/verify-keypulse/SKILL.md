---
name: verify-keypulse
description: Drive the KeyPulse CLI the way a user does and prove its behavior — for verifying a change to scoring, providers, or rendering, reproducing a reported bug, or checking a scan end to end. Runs the real `keypulse` commands in an isolated cache and captures command, exit code, and output as evidence.
---

# Verifying KeyPulse

KeyPulse is a **CLI** (the plugin's skill and slash commands are thin wrappers
that call the same binary). There is no server and no browser UI of its own, so
"driving the app" means running `keypulse` subcommands and reading their output
and exit codes. The browser bridge is an *upstream* transport, not a surface of
this app.

Every recipe below runs offline against a replay recording, so a verification
run never depends on Baidu or Google answering. That isolates the thing under
test — KeyPulse's own logic — from upstream flakiness.

## Launch

There is nothing to keep running. "Launch" is building the environment once,
then each recipe is a short-lived process:

```bash
# from the repo root
python3 -m compileall -q src bin/keypulse     # fails loudly on syntax errors
./bin/keypulse --version                      # prints: keypulse <version>
```

Ready means `--version` prints and exits 0. Do not proceed if it does not.

For the plugin surface (skill + slash commands) the equivalent launch is
loading the plugin:

```bash
claude --plugin-dir . plugin details keypulse   # expect skills >= 3
```

## Doctor

One read-only check — run it first whenever anything looks off:

```bash
./bin/keypulse doctor
```

Expect `doctor: ok` on stdout and exit `0`. It reports the binary, the version,
the cache directory in use, and whether the chain the recipes need is intact
(module imports, offline config, helper scripts). A non-zero exit prints the
first broken link; fix that before driving anything.

## Drive

Recipes live in [`features/`](features/README.md). Every recipe:
1. runs in its own scratch directory with its own cache (`KEYPULSE_CACHE_DIR`),
2. sets up via `scripts/run-recipe.sh` (materials the replay recording),
3. asserts on **exit code + parsed output**, not on prose.

Two conventions that keep runs comparable:

- **Isolate.** Never reuse the developer's `~/.cache/keypulse`; the helper sets
  a per-run `KEYPULSE_CACHE_DIR` under `$TMPDIR`.
- **Assert on JSON.** Use `--format json` and parse fields; the table renderer
  is for humans and its column widths change.

## Evidence

Capture into `artifacts/<feature-id>/` (relative to the scratch dir), never into
the repo:

| Proof | Artifact |
|---|---|
| command + exit code | `<id>.cmd.txt` (the exact command and `exit=`) |
| stdout/stderr | `<id>.out.json` / `<id>.err.txt` |
| ranking claims | the `ranked[]` array from `--format json` |

Proof standards:

- Exercise the real user path — the `keypulse` binary, not `python -c "import keypulse..."`.
- Capture the command *and* the resulting output; a screenshot of the final
  line alone does not show which command produced it.
- For a scoring change, include the component breakdown (`components`,
  `raw`) so the proof shows *why* a keyword ranked where it did.
- Never let cleanup delete `artifacts/`.

## Cleanup

```bash
scripts/cleanup.sh
```

Kills nothing (there are no daemons) and removes only the scratch directories
this run created, identified by the run id. Proof artifacts survive: cleanup
moves nothing out of `artifacts/`.

If a recipe needed the browser bridge (`keypulse providers` with live sources),
close only the tabs this run opened:

```bash
opencli browser close --session keypulse-baidu 2>/dev/null || true
```

## Helpers

All helpers are executable and take the run id as their first argument or via
`$RUN_ID`.

| Helper | What it does |
|---|---|
| `scripts/run-recipe.sh <run-id> <feature-id>` | creates the scratch dir, writes `.keypulse.json` + replay cache, prints the workspace path |
| `scripts/assert.sh <expected-exit> <cmd...>` | runs a command, records `exit=`, fails when the exit code differs |
| `scripts/cleanup.sh [run-id]` | removes scratch dirs for that run id |
| `scripts/smoke.sh` | launch + doctor + one end-to-end ranking recipe, used by the maintainer |

Example — a full proof of the ranking feature:

```bash
RUN_ID=kp-$(date +%s)
WS=$(scripts/run-recipe.sh "$RUN_ID" scan-ranking)
cd "$WS" && "$WS/../../bin/keypulse" ... # see features/scan-ranking.md for the literal command
```

`run-recipe.sh` prints the workspace path; every recipe below assumes you `cd`
there first.

## Maintenance

The feature map is the maintained source of truth. When the CLI grows a
subcommand or changes a flag, update the matching file in `features/` and add
the recipe to `scripts/smoke.sh` before the change lands.
