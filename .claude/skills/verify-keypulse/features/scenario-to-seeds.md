# Turn a scenario into keywords

A user describes what they are building and gets a usable keyword list, either
derived from the description (`seeds`), grown with autocomplete (`expand`), or
both at once (`scan --scenario`).

## Sub-features

- `seeds-zh` extracts whole Chinese phrases, not sliding-window fragments.
- `seeds-modifiers` appends intent suffixes (`怎么样`, `教程`, …).
- `expand-autocomplete` grows seeds with engine suggestions.
- `scan-scenario` runs the whole path from description to ranking.
- `seeds-empty` returns nothing (exit `0`) for a blank description.

## How to get to it (user POV)

- Run `keypulse seeds --scenario "..."`.
- Run `keypulse expand --keywords ...`.
- Run `keypulse scan --scenario "..."`.
- Ask the plugin skill to "research keywords for <idea>".

## Driving it with keypulse-cli

Preconditions:

- Workspace from `scripts/run-recipe.sh <run-id> scenario-to-seeds`.
- `KP` points at `bin/keypulse` in the repo.

- **Whole phrases.** Run `$KP seeds -s "我想做一款面向程序员的 AI 编程笔记软件，支持代码片段管理和知识库检索"`.
  Exit `0`; stdout contains `编程笔记软件` and `代码片段管理`.
- **No fragments.** The same output must not contain any line starting with
  `款面`, `向程`, or `做一` — those are the n-gram fragments the extractor is
  designed to avoid.
- **Modifiers.** Run `$KP seeds -s "AI笔记" --modifiers`. Exit `0`; stdout
  includes `AI笔记哪个好` (or another `AI笔记` + intent suffix).
- **JSON shape.** Run `$KP seeds -s "面向跨境电商卖家的 AI 选品分析工具" -f json`.
  Exit `0`; the document has `scenario` and a non-empty `seeds` array.
- **Blank scenario.** Run `$KP seeds -s "   "`. Exit `0`; no keywords printed.
- **Scenario scan.** Run `$KP scan --scenario "面向跨境电商卖家的 AI 选品分析工具" -r CN --top 3`.
  Exit `0` or `2`; if `0`, `ranked[]` is non-empty and every keyword is a whole
  phrase (no fragments).
- **Proof.** `scripts/assert.sh 0 seeds-zh -- $KP seeds -s "..."` plus a
  `grep -c '款面'` check that must be `0`.

## Gotchas

- Chinese text has no spaces, so the extractor splits on punctuation and
  function words. A scenario written as one long run-on sentence yields worse
  phrases; that is a property of the input, not a defect.
- `expand` hits autocomplete endpoints, so offline it returns just the seeds.
  Use it only in live mode when verifying expansion.
- The candidate list is capped by `--max-keywords`; a scenario that legitimately
  produces more keywords than the cap is not a failure.
- `seeds` is offline-safe: it never touches the network.
