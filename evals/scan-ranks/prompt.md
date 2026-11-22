---
name: scan-ranks
description: A scan must run the CLI and report the higher-weight keyword.
tags: [smoke, cli]
max_turns: 18
timeout_seconds: 600
allowed_tools: [Bash, Read]
---
用 KeyPulse 查一下 人工智能 和 量子计算 这两个词的指数，哪个更高？

直接跑扫描命令（若 `keypulse` 不在 PATH，就用
`"$CLAUDE_PLUGIN_ROOT/bin/keypulse"`），不要先探索仓库。扫一次就够，不要重复跑。

最后一行只写结论，格式：结论: <关键词> <分数>
