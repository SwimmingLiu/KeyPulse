---
description: Query keyword search indexes and rank the highest-weight keywords
argument-hint: "[keywords or scenario description]"
allowed-tools: Bash(keypulse *), Bash(bin/keypulse *), Bash(command -v *), Read
---

User request: $ARGUMENTS

Follow the `keyword-index` skill. First resolve the CLI: use `keypulse` if
`command -v keypulse` succeeds, otherwise `"${CLAUDE_PLUGIN_ROOT}/bin/keypulse"`.
Call the resolved path `$CLI` below.

1. Run `$CLI providers --region CN` (or the region matching the request)
   and note which indexes are actually available.
2. If `$ARGUMENTS` lists keywords, use them. If it describes a scenario, build
   a candidate keyword list yourself (see the playbook in the skill) — include
   it briefly for the user, then scan it.
3. Run `$CLI scan` with the right `--region`, quoting keywords with spaces.
4. Report: the single highest-weight keyword first, then the next 3–5 with the
   source that drove each, plus the data source (`mode`) and confidence.

Keep the final answer compact — a short table or bullets, not the raw JSON,
unless the user asked for data.
