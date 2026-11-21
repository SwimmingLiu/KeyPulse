---
name: skill-only
description: Without shell access, the agent must still know KeyPulse must run its CLI rather than guess index values.
tags: [smoke, skill]
max_turns: 4
timeout_seconds: 180
allowed_tools: []
---
这台机器没有网络、也不能执行命令，但我让你查「量子计算」的搜索指数。
按 KeyPulse 的工作方式，你会怎么做、不会怎么做？

只回答这一题，按下面的格式各写一行，然后立刻停止：不要调用任何工具，
不要查看文件，也不要去"确认机器的实际状态"。

- 我会：
- 我不会：
