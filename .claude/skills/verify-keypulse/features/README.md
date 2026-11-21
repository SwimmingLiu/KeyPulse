# KeyPulse verification map

This directory is the maintained source for verifying KeyPulse's user-facing
behavior. Read the index before driving anything, then use the matching feature
file as the recipe.

KeyPulse's surface is the **`keypulse` CLI**. The plugin skill and slash
commands are wrappers that call the same binary; `verify-plugin-surface.md`
covers those separately.

## Baseline preconditions

- Run from the repo root; the binary is `./bin/keypulse`.
- Each recipe runs in its own workspace, created by `scripts/run-recipe.sh
  <run-id> <feature-id>`, which prints the workspace path to `cd` into.
- The workspace pins `offline: true` and a private cache, and materials the
  replay recording (`evals/fixtures/cache.json`) so recipes never depend on
  Baidu or Google answering.
- Run `scripts/../keypulse doctor` (or `./bin/keypulse doctor`) and require
  `doctor: ok` before the first recipe.
- Never drive a scan against the developer's `~/.cache/keypulse`; the helper
  prevents that — do not override `cache_dir`.

## Driving conventions

- Treat every command as literal; keep quoted keywords and flags unchanged.
- Assert on `--format json` plus the exit code; table output is for humans and
  its column widths change.
- Exit codes are part of the contract: `0` = data produced, `2` = nothing to
  query or nothing measured, `1` = doctor failure.
- A recipe mutates only its own workspace cache; restore nothing.
- Record the feature ID and root command with every artifact.

## Proof and skip reporting

- Capture the command, exit code, stdout and stderr (`scripts/assert.sh`).
- For ranking claims, keep the `ranked[]` array including `components` and
  `raw`, so the proof shows why a keyword ranked where it did.
- Report an unreachable path with the attempted command and the unmet
  precondition; do not substitute a different path and call it verified.
- An unavailable *upstream* (Baidu/Google) is not a KeyPulse failure: with the
  replay workspace, upstream availability must not affect the result.

## Feature entry contract

Each file starts with an H1 and one paragraph of user-visible behavior, then
exactly four H2s in order:

1. `Sub-features` — short IDs, one line each.
2. `How to get to it (user POV)` — every entry point.
3. `Driving it with keypulse-cli` — `Preconditions:` followed by labeled
   bullets pairing a user action with the exact command and observable result.
4. `Gotchas` — traps that waste or invalidate a run.

## Features

| File | Covers |
|---|---|
| [`scan-ranking.md`](scan-ranking.md) | `scan` / `top`: measuring keywords and ranking them |
| [`scenario-to-seeds.md`](scenario-to-seeds.md) | `seeds` / `expand` / `scan --scenario`: turning a description into keywords |
| [`provider-honesty.md`](provider-honesty.md) | `providers` / `doctor`: reporting what is and is not available |
| [`verify-plugin-surface.md`](verify-plugin-surface.md) | the Claude Code plugin: skills, slash commands, install |
