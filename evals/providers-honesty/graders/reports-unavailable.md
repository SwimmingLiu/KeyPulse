---
type: regex
pattern: '^回答[:：].*(不可用|未启用|未配置|缺少|需要|没有|不可达|✗|Cookie|BDUSS)'
flags: im
---
The last line the agent writes must start with `回答:` and name a source that
is unavailable (or what it needs). An answer claiming everything works fails.

The `回答:` prefix is required by the prompt and appears nowhere in the CLI's
own output, so dumping the provider table without answering still fails.
