# Keyword expansion playbook

How to go from "here is my product scenario" to a keyword list worth measuring.
The CLI can grow a list mechanically (`--expand`), but a curated list beats an
expanded one because expansion drifts ("ai笔记本" is a laptop, not a notebook app).

## 1. Decompose the scenario

Pull out four kinds of terms and keep the user's own vocabulary:

| Type | Question | Example (AI 编程笔记软件) |
|---|---|---|
| 品类词 | What is this thing called? | AI 笔记、编程笔记软件、知识库工具 |
| 人群词 | Who is it for? | 程序员、开发者、独立开发者、学生 |
| 功能词 | What does it do? | 代码片段管理、知识库检索、AI 总结 |
| 竞品/替代 | What do people use now? | notion、obsidian、notion 替代、语雀 |

## 2. Attach intent modifiers

Chinese modifiers that consistently appear in real queries:

- 决策类：`哪个好`、`推荐`、`对比`、`排行榜`、`前十`
- 交易类：`多少钱`、`价格`、`免费`、`破解`（避开合规风险词）
- 学习类：`教程`、`入门`、`怎么用`、`原理`
- 替代类：`替代`、`平替`、`开源`

English: `best`, `vs`, `alternative`, `open source`, `pricing`, `tutorial`,
`for beginners`, `self-hosted`.

Cross core terms × modifiers, then **cut**: keep combinations that read like
something a person would actually type. "AI 笔记 哪个好" survives; "代码片段
管理 排行榜" does not.

## 3. Use the scenario's language and the market's language

- Chinese market → Chinese queries plus the English acronym people search
  (`ai coding`, `llm`, `rag`).
- International product → English queries; `--region US` or `GLOBAL`.
- Mixed → run two scans (`--region CN`, then `--region US`) and compare; the
  ranking often differs because WeChat/Baidu dominate one and Trends the other.

## 4. Size the list

- Comparison task ("which of these 6 should I pick") → 6–12 keywords.
- Discovery task ("find me keywords for this product") → 20–30, let `--top 10`
  do the trimming.
- Above ~30 keywords, expansion and scans slow down and hit rate limits; split
  into themes and run separately.

## 5. Sanity checks before reporting

- Does the top keyword feel like something with real demand, or is it an
  artifact of one noisy source? Check `components` in the JSON.
- Are two candidates near-duplicates (`AI笔记` vs `ai 笔记`)? The engine treats
  them as different strings; merge in your write-up.
- Is a low rank explained by missing data (no WeChat volume, no Baidu Index)
  rather than low demand? Say which.

## 6. Reading the score

| Field | Meaning |
|---|---|
| `score` | 0–100 weighted demand. Comparable only within one scan. |
| `confidence` | Share of usable sources that answered. < 0.5 = thin evidence. |
| `opportunity` | `score` discounted by competition (SERP/WeChat volume). |
| `components` | Per-source normalised value that drove the score. |
| `raw` | Original numbers (index value, article counts, pageviews). |
| `mode` | `index` = real index providers answered; `fallback` = proxies only. |
