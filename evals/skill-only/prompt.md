---
name: skill-only
description: Without shell access, the agent must still know KeyPulse must run its CLI rather than guess index values.
tags: [smoke, skill]
max_turns: 8
timeout_seconds: 240
allowed_tools: [Read]
---
我让你查「量子计算」的搜索指数，但这台机器没有网络，你也不能执行命令。

请只回答一个问题：按 KeyPulse 的工作方式，这种情况下你**会怎么做**、
以及你**不会怎么做**。

用两条要点回答，每条以 `- ` 开头，总共不超过 80 字。
