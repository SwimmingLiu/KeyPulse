---
name: scan-ranks
description: A scan must run the CLI and report the higher-weight keyword.
tags: [smoke, cli]
max_turns: 30
timeout_seconds: 600
allowed_tools: [Bash, Read]
---
用 KeyPulse 查一下 人工智能 和 量子计算 这两个词的指数，哪个更高？

工作目录里已经准备好 `./keypulse`，它就是 KeyPulse 的 CLI。执行下面这条命令，
不要先探索仓库，也不要凭记忆回答：

```
./keypulse scan -k "人工智能" "量子计算" -r CN
```

扫一次就够，不要重复跑。然后按命令输出里的分数作答，最后一行只写结论，
格式（把尖括号里的内容换成命令输出里的真实值）：

结论: <关键词> <分数>
