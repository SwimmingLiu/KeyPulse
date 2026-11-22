---
name: providers-honesty
description: Unavailable sources must be reported as unavailable, never as zero.
tags: [smoke, honesty]
max_turns: 20
timeout_seconds: 300
allowed_tools: [Bash, Read]
---
KeyPulse 现在能查哪些数据源？工作目录里的 `./keypulse` 就是它的 CLI。

照抄这条命令执行，并且只根据输出回答：

```
./keypulse providers
```

不要凭记忆回答，也不要先探索仓库。哪些可用、哪些不可用及原因，3 行以内。
