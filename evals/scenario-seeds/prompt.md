---
name: scenario-seeds
description: A scenario description must become real keyword seeds.
tags: [smoke, discovery]
max_turns: 12
timeout_seconds: 300
allowed_tools: [Bash, Read]
---
我想做一款面向程序员的 AI 编程笔记软件，支持代码片段管理和知识库检索。

先别查指数，只告诉我你会用哪些关键词去查——列出 6 个左右的候选关键词。

规则：
- 每条候选词一行，行首用 `- `。
- 只输出关键词列表，不要解释。
