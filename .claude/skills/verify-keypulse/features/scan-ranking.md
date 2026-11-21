# Scan and rank keywords

A user names keywords (or describes a scenario) and gets them measured and
ranked by weight, with the data source labelled and unavailable sources
explained instead of silently dropped.

## Sub-features

- `scan-basic` measures a keyword list and prints a ranked table.
- `scan-json` returns the same ranking as machine-readable JSON with components.
- `scan-top` returns only the leaders, for a quick answer.
- `scan-explain` reports why a source is missing (`未启用来源` / `查询失败来源`).
- `scan-exit` returns exit `0` when data was produced, `2` when nothing was.
- `scan-mode` labels index vs proxy data and renames the score column to match.

## How to get to it (user POV)

- Run `keypulse scan --keywords ...` in a terminal.
- Run `keypulse top --keywords ...` when only the leaders are wanted.
- Ask the plugin skill in natural language (see `verify-plugin-surface.md`).

## Driving it with keypulse-cli

Preconditions:

- Workspace created by `scripts/run-recipe.sh <run-id> scan-ranking`, and you
  have `cd`-ed into it.
- `./bin/keypulse doctor` (from the repo) printed `doctor: ok`.
- The replay recording contains `人工智能` and `量子计算` (it does).

Inside the workspace, `KP=/path/to/repo/bin/keypulse`:

- **Ranked table.** Run `$KP scan -k "人工智能" "量子计算" -r CN --top 2`. Exit
  `0`; stdout has a header row containing `关键词`, two numbered rows, and a
  `数据来源:` line.
- **JSON ranking.** Run `$KP scan -k "人工智能" "量子计算" -r CN --format json`.
  Exit `0`; the parsed document has `mode`, `ranked[]` and `summary`. Each
  `ranked[]` entry has `keyword`, `score`, `confidence`, `components`, `raw`.
- **Winner is the higher index.** In that JSON, `ranked[0].keyword` is
  `人工智能` and `ranked[0].score` > `ranked[1].score`; `ranked[0].raw` contains
  a `baidu_index` key whose value exceeds the second keyword's.
- **Leaders only.** Run `$KP top -k "人工智能" "量子计算" -r CN --top 1`. Exit
  `0`; stdout names `人工智能` and no second numbered row.
- **Truthful provenance.** Run `$KP scan -k "人工智能" -r CN --format json` and
  read `mode`. With the replay recording it is `index`; the table renders the
  score column as `指数分` (not `热度分`).
- **No keywords.** Run `$KP scan`. Exit `2`; stderr/stdout explains that
  `--keywords` or `--scenario` is required. Nothing is invented.
- **Proof.** `scripts/assert.sh 0 scan-json -- $KP scan -k "人工智能" "量子计算"
  -r CN --format json` writes `artifacts/scan-ranking/scan-json.{cmd,out,err}.txt`.

## Gotchas

- The table renderer's column widths vary with keyword length; never assert on
  spacing. Parse the JSON instead.
- `--region` changes which providers are eligible (Baidu is `CN`-only). A `US`
  scan legitimately has no `baidu_index`.
- `score` is comparable only within one scan. Comparing two separate runs by
  raw score is meaningless.
- Offline mode without a replay recording produces zeros with exit `2` — that
  is correct behavior, not a bug to "fix" by disabling offline.
- A keyword Baidu does not index appears as `baidu_index` with value `0` and
  `detail.unindexed: true`, plus a `未收录` line. That is data, not a failure.
