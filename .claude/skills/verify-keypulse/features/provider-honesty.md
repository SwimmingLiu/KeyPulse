# Report what is actually available

A user needs to know, before trusting a number, which sources answered and why
the others did not. KeyPulse answers this two ways: a static inventory
(`providers`) and a self-check (`doctor`).

## Sub-features

- `providers-list` lists every provider with availability and the reason.
- `providers-region` filters by what can serve that region.
- `providers-json` returns the same inventory as JSON for scripting.
- `doctor-ok` reports the core chain healthy and exits `0`.
- `doctor-fail` exits `1` when the core chain is broken, naming the link.
- `doctor-optional` never fails on an absent browser bridge or cookie.

## How to get to it (user POV)

- Run `keypulse providers --region CN`.
- Run `keypulse doctor`.

## Driving it with keypulse-cli

Preconditions:

- `KP` points at `bin/keypulse`.
- For the failure case only: a workspace whose `.keypulse.json` sets
  `cache_dir` to a directory that cannot be created.

- **Inventory.** Run `$KP providers -r CN`. Exit `0`; every known provider has
  a line, marked `✓` or `✗`, each with a short reason.
- **Region scope.** Run `$KP providers -r US -f json`. Exit `0`; the
  `baidu_index` entry has `supports_region: false`. Run with `-r CN` and it is
  `true`.
- **JSON shape.** Each entry has `name`, `label`, `regions`, `kind`,
  `supports_region`, `available`, `note`.
- **Honest absence.** With no Baidu cookie and no replay, `baidu_index` reports
  `available: false` and a `note` naming what it needs — never `available: true`
  with a zero value.
- **Doctor ok.** Run `$KP doctor`. Exit `0`; stdout ends with `doctor: ok` and
  every line is `✓` or `·` (optional).
- **Doctor JSON.** Run `$KP doctor -f json`. Exit `0`; the document has
  `ok: true` and a `checks[]` array including `package`, `providers`,
  `cache_dir`, `python`.
- **Optional never fatal.** Disable the bridge: run
  `KEYPULSE_BROWSER=off $KP doctor`. Exit is still `0`; the `browser_bridge`
  line says it is disabled.
- **Doctor fails honestly.** In a scratch workspace, write `.keypulse.json`
  with `"cache_dir": "/proc/nope/keypulse"` and run `$KP doctor`. Exit `1`;
  stdout names `cache_dir` as the failure.
- **Proof.** `scripts/assert.sh 0 doctor -- $KP doctor` and
  `scripts/assert.sh 1 doctor-fail -- $KP doctor` (second, in the broken
  workspace).

## Gotchas

- `providers` performs live health checks, so in live mode it can take a few
  seconds per source and can differ run to run. `doctor` is read-only and fast.
- `doctor` never reports the browser bridge as a failure; absence of the bridge
  degrades data quality, not correctness.
- A provider listed as unavailable is not an error state — the scan is meant to
  proceed with the others and say so.
