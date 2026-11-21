---
name: skill-only
description: Without shell access, the agent must still know KeyPulse must run its CLI rather than guess index values.
tags: [smoke, skill]
max_turns: 10
timeout_seconds: 240
allowed_tools: [Read]
---
请先用一句话回答下面的问题，再决定是否继续做别的事：

这台机器没有网络、也不能执行命令，但我让你查「量子计算」的搜索指数。
按 KeyPulse 的工作方式，你会怎么做、不会怎么做？

回答格式（先输出这两行，再做其他事）：
- 我会：
- 我不会：
