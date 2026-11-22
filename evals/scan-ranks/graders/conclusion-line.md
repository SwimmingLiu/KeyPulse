---
type: regex
pattern: '^回答[:：].*(人工智能|量子计算).*\d'
flags: m
---
The last line the agent writes must start with `回答:` and carry one of the two
keywords together with a number it could only have got from running the scan.

The `回答:` prefix is required by the prompt and appears nowhere in the CLI's
own output, so a run that dumps the table but never answers still fails.
