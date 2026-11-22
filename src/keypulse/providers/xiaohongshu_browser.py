"""小红书 (Xiaohongshu / RED) demand via the browser bridge.

RED publishes no search index.  What it does expose, to a logged-in browser, is
the search result page: which notes rank for a keyword and how much engagement
each has (likes, collects, comments).  KeyPulse uses that as a *volume proxy*,
never as an "index" — the provider is registered in the corpus tier so a
report built on it says 热度分, not 指数分.

Why the page and not the API: as of this writing RED's JSON endpoints
(``edith.xiaohongshu.com`` and ``so.xiaohongshu.com/api/sns/web/v2/search/notes``)
answer ``500 create invoker failed`` / ``406`` to a fetch issued from the very
page that then renders the same data normally.  The DOM path works and needs no
request signing, so that is the one this provider takes.

The measured value is the mean interaction count over the first page of
results: a keyword nobody talks about has notes with a handful of likes, a
popular one has notes in the thousands.
"""

from __future__ import annotations

import json
import urllib.parse

from .. import opencli as bridge
from ..models import Signal
from .base import Provider, normalise_region

SESSION = "keypulse-xhs"
SEARCH_URL = "https://www.xiaohongshu.com/search_result?keyword="

#: Scroll until enough cards are rendered, then read title/likes/author.
#: ``__KEYWORD__`` is substituted by the caller.  RED renders lazily, so an
#: unscrolled page yields ~20 cards with most of their text missing.
READ_SCRIPT = r"""
(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const num = (s) => {
    if (s == null) return null;
    const m = String(s).replace(/\s+/g, '').match(/([\d.]+)\s*(万|w)?/i);
    if (!m) return null;
    const v = Number(m[1]);
    return m[2] ? v * 1e4 : v;
  };
  const deadline = Date.now() + 25000;
  // Wait for either results or an explicit "nothing found" state.
  while (Date.now() < deadline) {
    const t = document.body.innerText || '';
    if (/请先登录|登录后查看|验证/.test(t) && !document.querySelector('section.note-item')) {
      return JSON.stringify({ error: '需要登录 www.xiaohongshu.com（在浏览器登录后运行 keypulse login --provider xiaohongshu）' });
    }
    if (document.querySelector('section.note-item') || /暂无|没有找到/.test(t)) break;
    await sleep(600);
  }
  const cards = () => [...document.querySelectorAll('section.note-item')];
  if (!cards().length) return JSON.stringify({ error: '搜索结果未渲染（登录态或空结果）' });

  // Lazy rendering: scroll until the count stops growing.
  let last = 0;
  for (let i = 0; i < 8; i++) {
    window.scrollBy(0, 1400);
    await sleep(700);
    const n = cards().length;
    if (n === last && n >= 24) break;
    last = n;
  }

  const rows = [];
  for (const s of cards()) {
    const likeEl = s.querySelector('.like-wrapper .count');
    const titleEl = s.querySelector('.footer .title span, .title span, .title');
    const authorEl = s.querySelector('.author-wrapper .name, .name');
    const title = titleEl ? titleEl.innerText.trim() : '';
    rows.push({
      title: title.slice(0, 60),
      likes: num(likeEl && likeEl.innerText),
      author: authorEl ? authorEl.innerText.trim() : null,
    });
  }
  const withLikes = rows.filter((r) => typeof r.likes === 'number');
  const total = withLikes.reduce((a, r) => a + r.likes, 0);
  const kw = __KEYWORD__;
  const onTopic = kw ? rows.filter((r) => r.title && (r.title.includes(kw) || kw.split('').some((c) => r.title.includes(c)))).length : 0;
  return JSON.stringify({
    notes: rows.length,
    notes_with_likes: withLikes.length,
    likes_total: total,
    likes_mean: withLikes.length ? Math.round(total / withLikes.length) : 0,
    on_topic: onTopic,
    sample_titles: rows.slice(0, 3).map((r) => r.title),
  });
})()
"""


class XiaohongshuBrowser(Provider):
    name = "xiaohongshu_notes"
    label = "小红书笔记互动"
    metric = "engagement_mean"
    unit = "hits"
    kind = "pages"
    tier = "corpus"
    access = "browser"
    regions = ("CN",)
    requires_session = True

    def check(self) -> tuple[bool, str]:
        try:
            if any(self.settings.cache_dir.glob(f"replay-{self.name}-*.json")):
                return True, "ok (replay)"
        except OSError:
            pass
        ok, reason = bridge.available()
        if not ok:
            return False, f"需要浏览器桥接：{reason}"
        try:
            from .. import sessions

            state = sessions.recall(self.name, self.settings)
        except Exception:  # noqa: BLE001
            state = None
        if state is not None and state.logged_in is False:
            return False, "需要登录 www.xiaohongshu.com（keypulse login --provider xiaohongshu）"
        if state is not None and state.logged_in is True:
            return True, "ok (browser bridge, 已登录)"
        return True, "ok (browser bridge; 登录态未确认，可运行 keypulse login)"

    def signal(self, keyword: str, region: str = "CN") -> Signal:
        recorded = self._replay(keyword)
        if recorded:
            return self._replayed_signal(keyword, region, recorded)
        return self.signals([keyword], region)[0]

    def signals(self, keywords: list[str], region: str = "CN") -> list[Signal]:
        recorded = [self._replay(kw) for kw in keywords]
        if all(recorded):
            return [self._replayed_signal(kw, region, e) for kw, e in zip(keywords, recorded)]
        return [self._one(kw, region) for kw in keywords]

    def _one(self, keyword: str, region: str) -> Signal:
        url = SEARCH_URL + urllib.parse.quote(keyword)
        try:
            bridge.open_page(self.settings, url, session=SESSION)
            payload = bridge.eval_js(
                self.settings,
                READ_SCRIPT.replace("__KEYWORD__", json.dumps(keyword, ensure_ascii=False)),
                session=SESSION,
                timeout=max(75.0, self.settings.timeout * 5),
            )
            data = json.loads(payload) if isinstance(payload, str) else payload
        except Exception as exc:  # noqa: BLE001 - one blocked source must not sink the scan
            return self._failure(keyword, f"{type(exc).__name__}: {exc}", region)

        if "error" in data:
            return self._failure(keyword, str(data["error"]), region)
        if not data.get("notes"):
            return self._failure(keyword, "没有搜索结果（可能未登录或该词无内容）", region)
        return Signal(
            keyword=keyword,
            provider=self.name,
            metric=self.metric,
            value=float(data.get("likes_mean") or 0),
            unit=self.unit,
            region=normalise_region(region),
            detail={
                "notes": data.get("notes"),
                "likes_total": data.get("likes_total"),
                "on_topic": data.get("on_topic"),
                "sample_titles": data.get("sample_titles"),
                "note": "小红书没有公开搜索指数；这是首页笔记的平均点赞数，属内容量代理",
                "transport": "opencli",
            },
        )
