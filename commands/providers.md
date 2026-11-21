---
description: Show which keyword data sources are reachable right now
argument-hint: "[region: CN|US|GLOBAL]"
allowed-tools: Bash(keypulse *), Bash(bin/keypulse *), Bash(command -v *)
---

Resolve the CLI first: `keypulse` if `command -v keypulse` succeeds, else
`"${CLAUDE_PLUGIN_ROOT}/bin/keypulse"`. Then run:

```
$CLI providers --region ${ARGUMENTS:-CN}
```

Report in one compact table: which sources are available, which need
configuration (Baidu Index needs `KEYPULSE_BAIDU_COOKIE`), and which are
currently blocked by rate limiting. Note that 百度指数 / Google Trends need
extra setup on this machine if they show as unavailable, and what the scan will
use instead.
