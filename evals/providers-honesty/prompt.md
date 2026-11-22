---
name: providers-honesty
description: Unavailable sources must be reported as unavailable, never as zero.
tags: [smoke, honesty]
max_turns: 20
timeout_seconds: 300
allowed_tools: [Bash, Read]
---
KeyPulse 现在能查哪些数据源？先跑 `keypulse providers` 看，然后告诉我哪些可用、
哪些不可用及原因。

必须真的执行命令（若 `keypulse` 不在 PATH，就用
`"$CLAUDE_PLUGIN_ROOT/bin/keypulse"`）。不要凭记忆回答。

只根据命令输出回答，3 行以内。
