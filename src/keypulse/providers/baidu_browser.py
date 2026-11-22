"""百度指数 via the browser bridge.

The JSON API (``/api/SearchApi/index``) is heavily rate-limited: even from
inside the browser it answers ``status: 10018`` (异常访问).  The rendered UI,
however, works — searching a keyword in the trend page puts the
"搜索指数概览" table in the DOM with the overall daily mean.

So this provider drives the page the way a person would: type the keyword,
press the send button, poll for the row, read it.  The page's own request is
what passes Baidu's checks; there is no header or cookie trick to copy.
"""

from __future__ import annotations

import json

from .. import opencli as bridge
from ..models import Signal
from .base import Provider, normalise_region

SESSION = "keypulse-baidu"
INDEX_URL = "https://index.baidu.com/v2/main/index.html"

#: Type into the search box, press its send button, then poll for the row.
#: ``__KEYWORD__`` is replaced with a JSON string literal by the caller.
#: Written as a raw string so the JS backslashes survive.
WAIT_SCRIPT = r"""
(async () => {
  const kw = __KEYWORD__;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const num = (s) => Number(String(s).replace(/,/g, ''));
  const input = document.querySelector('input.search-input');
  if (!input) return JSON.stringify({ error: 'no search input (login wall?)' });
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  input.focus();
  setter.call(input, kw);
  input.dispatchEvent(new Event('input', { bubbles: true }));
  await sleep(800);
  const send = document.querySelector('.search-input-cancle') ||
               document.querySelector('.search-input-operate img');
  if (!send) return JSON.stringify({ error: 'search button not found' });
  send.click();
  const esc = kw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const rowRe = new RegExp(esc + '\\s+([\\d,]+)\\s+([\\d,]+)\\s+(-?\\d+)%');
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    const text = (document.body.innerText || '').replace(/\s+/g, ' ');
    if (/异常访问|安全验证/.test(text)) {
      return JSON.stringify({ error: 'blocked by Baidu anti-bot' });
    }
    const m = text.match(rowRe);
    if (m) {
      return JSON.stringify({
        overall: num(m[1]), mobile: num(m[2]), yoy: m[3] + '%', url: location.href
      });
    }
    await sleep(400);
  }
  return JSON.stringify({ error: 'overview table did not render within 20s' });
})()
"""


def _has_replay(provider: str, settings) -> bool:
    try:
        return any(settings.cache_dir.glob(f"replay-{provider}-*.json"))
    except OSError:
        return False


class BaiduIndexBrowser(Provider):
    name = "baidu_index"
    label = "百度指数"
    metric = "index"
    unit = "raw"
    kind = "index"
    tier = "index"
    access = "browser"
    regions = ("CN",)

    def check(self) -> tuple[bool, str]:
        if _has_replay(self.name, self.settings):
            return True, "ok (replay)"
        ok, reason = bridge.available()
        if not ok:
            if self.settings.baidu_cookie:
                return True, "ok (Cookie)"
            return False, f"需要浏览器桥接：{reason}"
        try:
            from .. import sessions

            state = sessions.recall(self.name, self.settings)
        except Exception:  # noqa: BLE001
            state = None
        if state is not None and state.logged_in is False:
            return False, "需要登录 index.baidu.com（keypulse login --provider baidu）"
        if state is not None and state.logged_in is True:
            return True, "ok (browser bridge, 已登录)"
        return True, "ok (browser bridge)"

    def signal(self, keyword: str, region: str = "CN") -> Signal:
        recorded = self._replay(keyword)
        if recorded:
            return self._replayed_signal(keyword, region, recorded)
        return self.signals([keyword], region)[0]

    def signals(self, keywords: list[str], region: str = "CN") -> list[Signal]:
        """Read every keyword in one browser round-trip."""
        recorded = [self._replay(kw) for kw in keywords]
        if all(recorded):
            return [self._replayed_signal(kw, region, entry) for kw, entry in zip(keywords, recorded)]
        outcomes = read_many(self.settings, keywords, region)
        results: list[Signal] = []
        for keyword in keywords:
            outcome = outcomes.get(keyword) or {"error": "no result"}
            if outcome.get("unindexed"):
                # Baidu itself says the word is not indexed. That is data, not
                # an error: report a zero with the reason attached so the
                # report can distinguish it from "we failed to look".
                results.append(
                    Signal(
                        keyword=keyword,
                        provider=self.name,
                        metric=self.metric,
                        value=0.0,
                        unit=self.unit,
                        region=normalise_region(region),
                        detail={
                            "unindexed": True,
                            "note": "百度指数未收录该词（可在百度指数后台创建后查询）",
                            "transport": "opencli",
                        },
                    )
                )
                continue
            if "error" in outcome:
                results.append(self._failure(keyword, str(outcome["error"]), region))
                continue
            results.append(
                Signal(
                    keyword=keyword,
                    provider=self.name,
                    metric=self.metric,
                    value=float(outcome["overall"]),
                    unit=self.unit,
                    region=normalise_region(region),
                    detail={
                        "mobile": outcome.get("mobile"),
                        "yoy": outcome.get("yoy"),
                        "window": "近 30 天日均",
                        "transport": "opencli",
                    },
                )
            )
        return results


#: Batch version: for each keyword, type, submit, and wait for its own row.
#: Written as a raw string so the JS backslashes survive.
BATCH_SCRIPT = r"""
(async () => {
  const kws = __KEYWORDS__;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const num = (s) => Number(String(s).replace(/,/g, ''));
  const out = {};
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  const waitForInput = async () => {
    for (let i = 0; i < 40; i++) {
      const el = document.querySelector('input.search-input');
      if (el) return el;
      await sleep(250);
    }
    return null;
  };
  for (const kw of kws) {
    try {
      const input = await waitForInput();
      if (!input) { out[kw] = { error: 'no search input (login wall?)' }; continue; }
      input.focus();
      setter.call(input, kw);
      input.dispatchEvent(new Event('input', { bubbles: true }));
      await sleep(700);
      const send = document.querySelector('.search-input-cancle') ||
                   document.querySelector('.search-input-operate img');
      if (!send) { out[kw] = { error: 'search button not found' }; continue; }
      send.click();
      const esc = kw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const rowRe = new RegExp(esc + '\\s+([\\d,]+)\\s+([\\d,]+)\\s+(-?\\d+)%');
      const unindexed = new RegExp(esc + '\\s*未被收录');
      const deadline = Date.now() + 20000;
      let done = false;
      while (Date.now() < deadline) {
        const text = (document.body.innerText || '').replace(/\s+/g, ' ');
        if (/异常访问|安全验证/.test(text)) { out[kw] = { error: 'blocked by Baidu anti-bot' }; done = true; break; }
        if (unindexed.test(text) && !/概览/.test(text)) {
          out[kw] = { unindexed: true }; done = true; break;
        }
        const m = text.match(rowRe);
        if (m) {
          out[kw] = { overall: num(m[1]), mobile: num(m[2]), yoy: m[3] + '%' };
          done = true;
          break;
        }
        await sleep(400);
      }
      if (!done) out[kw] = { error: 'overview table did not render within 20s' };
    } catch (e) {
      out[kw] = { error: String(e).slice(0, 120) };
    }
  }
  return JSON.stringify(out);
})()
"""


def read_many(settings, keywords: list[str], region: str = "CN") -> dict:
    """Read several keywords in one browser round-trip.

    Navigating per keyword costs ~7s each; this navigates once and then clears
    the search box between queries, which is ~2s each after the first.
    """
    script = BATCH_SCRIPT.replace("__KEYWORDS__", json.dumps(keywords, ensure_ascii=False))
    try:
        bridge.open_page(settings, INDEX_URL, session=SESSION)
        # The batch waits up to 20s per keyword for its table to render; give
        # the browser call room for all of them plus navigation overhead.
        budget = 30.0 + 25.0 * max(1, len(keywords))
        payload = bridge.eval_js(settings, script, session=SESSION, timeout=budget)
        data = json.loads(payload) if isinstance(payload, str) else payload
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001 - bridge problems degrade to failures
        return {kw: {"error": f"{type(exc).__name__}: {exc}"} for kw in keywords}
