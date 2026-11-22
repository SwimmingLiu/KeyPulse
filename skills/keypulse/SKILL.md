---
name: keyword-index
description: Research keyword search demand and pick the highest-weight keywords. Use when the user asks for 关键词指数 / 关键词权重 / 搜索指数 / 关键词推荐 / keyword research / SEO or GEO keyword selection / "which keywords should I target" / wants keywords derived from a product scenario, or asks to compare keywords by search volume or index. Runs the bundled keypulse CLI (百度指数, Google Trends, 抖音指数, 微信指数, Wikipedia and others) and reports a ranked shortlist with the evidence behind each score.
allowed-tools: Bash(keypulse *), Bash(bin/keypulse *), Bash(command -v *), Bash(python3 *), Read, Write
---

# Keyword index research (KeyPulse)

## Step 0 — resolve the CLI before anything else

Every command below is written as `keypulse …`, which assumes the binary is on
`PATH`. That holds for an interactive install but **not** inside sandboxes or
CI, and the binary path is the one thing that silently breaks every later step.

Resolve it once, at the start of the task:

```bash
command -v keypulse || echo "NOT_ON_PATH"
"${CLAUDE_PLUGIN_ROOT}/bin/keypulse" --version
```

- If `command -v keypulse` printed a path, use `keypulse` for the rest of the
  session.
- Otherwise use `"${CLAUDE_PLUGIN_ROOT}/bin/keypulse"` literally — the value is
  substituted before you see this text, so it points at this plugin's install
  directory wherever it lives.
- If neither works, say the CLI could not be found and stop. Do not guess at
  indexes.

Do not spend turns hunting for the binary (`ls`, `which -a`, globbing the repo):
one `command -v` plus one `${CLAUDE_PLUGIN_ROOT}` call is the whole check.

## What to answer

1. **What are the keyword indexes?** — for the keywords they named, or for keywords you propose from their scenario.
2. **Which keywords are worth the most?** — a ranked shortlist where the top item is the highest-weight keyword.

The CLI does the measuring. Your job is to choose good keywords, run the right
command, and explain the result honestly.

## 0. Check what is actually available first

Run this before promising 百度指数 or Google Trends numbers:

```bash
keypulse providers --region CN   # or "$CLI" providers --region CN
```

Read the result and set expectations in one short sentence before continuing:

| What you see | What it means | What to say |
|---|---|---|
| `baidu_index` ✗ "需要 KEYPULSE_BAIDU_COOKIE" | No Baidu Index session | 百度指数需要登录；`keypulse login --provider baidu` 可从浏览器导入 Cookie |
| `baidu_index` ✗ "浏览器没有登录 index.baidu.com" | The local browser has no Baidu session | 让用户在浏览器登录 index.baidu.com，再 `keypulse login --provider baidu` |
| `baidu_index` ✗ "blocked by Baidu anti-bot" | Baidu served 异常访问 | 百度风控；降低频率后重试，不要当成没数据 |
| `google_trends` ✗ "HTTP 429" | This IP is rate-limited | Google Trends 当前被限流；改用其他来源或稍后重试 |
| `douyin_index` ✗ "需要登录 creator.douyin.com" | The bridge browser is not logged in to the creator centre | 抖音指数需要在浏览器里登录；先说清楚，不要编数字 |
| `xiaohongshu_notes` ✗ "需要登录 www.xiaohongshu.com" | The bridge browser is not logged in to RED | 小红书需要在浏览器里登录 |
| `wechat_index` ✗ "需要 openid + search_key" | No 微信指数 credential yet | 微信指数要一条命令刷新凭据（见下） |
| `wechat_index` ✗ 凭据失效 | The `search_key` aged out (~30 min, measured) | 先 `keypulse wechat-open` 打开小程序，再 `keypulse wechat-token`，然后重扫 |
| All ✓ | Real indexes available | 直接用指数排序 |

`keypulse login` (no flags) probes every login-gated source and prints, per
source, 已登录 / 未登录 plus the URL to log in at; `--json` gives the same as
data. Use it instead of guessing from a failed scan.

Two kinds of gate — do not conflate them:

* **Storable**: 百度指数 takes a cookie, so `keypulse login --provider baidu`
  imports it from the browser and saves it. A saved cookie is evidence of a
  login, not a promise that Baidu's anti-bot (`status 10018`) will accept the
  request.
* **Browser-only**: 抖音指数 and 小红书 sign their APIs per page load. There
  is nothing to copy; the browser must stay logged in. Never report these as
  已登录 unless the probe said so.

微信指数 credentials are read from the WeChat client — no proxy, no phone
capture. The `search_key` lasts about **30 minutes** (measured: minted 22:51,
still serving 23:20, rejected 23:22), so refresh right before a scan:

```bash
keypulse wechat-open       # opens the mini-program (WeChat mints a new key)
keypulse wechat-token      # reads openid + search_key from WeChat's cache
keypulse scan ...          # now
```

Opening the mini-program is enough because the client renews the key itself:
its `cgi/cgi.js` treats `code -10000` as "session expired", runs `wx.login` +
`weapplogin`, and retries. It only skips that when `third_session` is already
stored, which is why a *reopen* can mint nothing while a launch after an
expiry always does.

The scan itself also re-reads WeChat's cache on every call, so if the client
logged in more recently than the saved copy, the newer key is used
automatically — a stale saved key can no longer mask a fresh one.

If a scan answers `code -10004` ("process failed") that is **not** a
credential problem: `start_ymd` / `end_ymd` were sent as integers instead of
`YYYYMMDD` strings. Check the request body before blaming the key.

`wechat-open` stops before clicking a search result (that position is not
stable); if it reports it could not get focus, tell the user to open it by
hand — click WeChat's search box, type `微信指数`, open it — then run
`keypulse wechat-token`. `wechat_desktop_index` is the credential-free
fallback (screenshots the open window), so a scan still works when the token
is stale.

To open it by hand, prefer **search** over the 最近使用 list — 最近使用 gets
overwritten as other mini-programs are used:

1. **Search** — click the search box at the top of the WeChat main window,
   type `微信指数`, open it from the results.
2. **Mini Programs → 最近使用** — only while 微信指数 is still in that list.

Check before clicking: `osascript -e 'tell application "System Events" to tell
process "WeChat" to get name of every window'`. If the list already contains
`微信指数`, it is open and the key is fresh — just run `keypulse wechat-token`.

Once it opens, the client calls `weapplogin` and the key is minted — no search
*inside* the mini-program needed. Verify with
`python3 scripts/wechat-index/read_token.py --json` (`key_age_seconds` under
~1500 means a usable key).

Two traps when automating the click: another app can steal focus between
`activate` and the click (check `frontmost` first, and never type after losing
it — the text lands in a different app), and the screenshot is Retina 2× —
halve image coordinates before adding the window origin.

If the user has a Baidu cookie and wants the real 百度指数, tell them once:

```
keypulse login --provider baidu      # imports it from the browser they use
keypulse login --baidu-cookie "BDUSS=...; ..."   # or paste it by hand
```

百度指数 runs through the local Chrome by default (`src/keypulse/providers/
baidu_cdp.py`), injecting that cookie into a headless browser and reading the
搜索指数概览 table. Its JSON API returns `status 10018` even from inside the
page, so do not try to "fix" a 10018 by retrying the API — use the browser
transport. If it reports `blocked by Baidu anti-bot`, that is Baidu throttling,
not a missing session: slow down rather than re-authenticating.

Do **not** report a proxy number as if it were 百度指数 / Google Trends. The
CLI labels the data source (`mode: index` vs `mode: fallback`) and renames the
score column accordingly (`指数分` vs `热度分`); keep that label in your answer.

## 1. Get the keyword list

**User listed keywords** → use them directly.

**User described a scenario** → propose keywords yourself; do not just echo
the scenario. Read `${CLAUDE_SKILL_DIR}/references/keyword-playbook.md` for the
expansion patterns, then build a candidate list in this shape:

- 3–5 core terms (产品品类、人群、技术名词)
- 8–15 long-tail variants (核心词 + 意图后缀: 推荐/对比/价格/教程/替代/开源/工具)
- 2–3 竞品或替代方案 terms
- For Chinese scenarios, mix in the English forms people search (e.g. `ai coding`)

Cap the final list at `--max-keywords` (default 25) — small lists finish fast
and keep the sources polite. If the user gave a scenario in Chinese and wants
Chinese demand, use `--region CN`; for an international/English product use
`--region US` or the default `GLOBAL`.

Show the candidate list to the user *briefly* (grouped bullets, not a wall of
text) and then run the scan — do not stop and wait for approval unless the list
is very large or the user asked to review it first.

## 2. Measure

A multi-word keyword is one list item; quote it. `-k` takes several values, so
these two are equivalent and both correct:

```bash
keypulse scan -k "AI编程" "AI笔记" "cursor 替代" -r CN
keypulse scan --keywords "AI编程" "AI笔记" "cursor 替代" --region CN
```

```bash
# keywords the user named
keypulse scan --keywords "AI编程" "AI笔记" "AI写代码" --region CN --top 10

# expansion from an existing seed (autocomplete from Baidu/Google/360/Sogou)
keypulse scan --keywords "AI编程" --expand --max-keywords 20 --region CN

# your own candidate list, technical audience (adds GitHub/HN weight)
keypulse scan --keywords "..." "..." --region US --include-audience
```

Notes that matter:

- Lists are space-separated; quote keywords containing spaces.
- `--expand` costs one autocomplete round per seed per engine — use it to grow
  a small seed set, not on a list you already curated.
- Default output is a table. Use `--format json` when you need every raw number,
  `--format md` when you want to hand the table straight to the user.
- A scan takes ~10–60s (network-bound). Do not re-run it just to reformat;
  re-render from the JSON you already have.

## 3. Decide and explain

`keypulse top --keywords ... --top 3` returns just the leaders. When you report:

- Lead with the answer: **the single highest-weight keyword** and its score.
- Give the next 3–5 with a one-line reason each (which source drove it).
- State the data source and confidence. Low confidence (< 0.5) or `mode:
  fallback` means the ranking is indicative — say so plainly.
- Flag 机会分 (opportunity = demand discounted by competition) when the user's
  goal is picking keywords to rank for, not just measuring popularity.
- If a keyword the user expected ranks low, say why (e.g. no WeChat article
  volume, thin Wikipedia coverage) rather than silently reordering.

Never invent an index value. If every source failed, report that and stop.

## 4. When the user wants a durable artifact

Write the full JSON with `--format json > report.json` and/or a Markdown table
if they ask for a file. Do not write files they did not ask for.

## Failure handling

| Symptom | Action |
|---|---|
| `429` / "rate-limiting" | Report it; suggest retry later or a proxy. Do not loop. |
| "触发反爬验证" | IP is throttled on that source; the scan already moved on. |
| All sources ✗ | Say so with the provider table; do not fabricate. |
| `mode: fallback` | Ranking is proxy-based; present it as 热度参考, say 不是搜索指数. |
| Wrong/odd keywords in output | Tighten the candidate list; `--expand` can drift to unrelated long tails. |
