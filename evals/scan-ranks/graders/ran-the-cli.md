---
type: tool_used
tool: Bash
input_match: 'keypulse\b[^\n]*\b(scan|top)\b'
min: 1
---
The agent must actually invoke KeyPulse's scan/top subcommand — via `keypulse`
on PATH or the bundled `bin/keypulse` — rather than inventing numbers.
