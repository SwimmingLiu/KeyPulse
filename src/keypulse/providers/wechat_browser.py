"""Sogou WeChat article count via the browser bridge.

Sogou's WeChat vertical blocks plain HTTP requests with an anti-spider page
(``请输入验证码``), which is why the HTTP provider often fails.  From a real
browser it renders normally and states the total: ``找到约3,957条结果``.
"""

from __future__ import annotations

import json
import urllib.parse

from .. import opencli as bridge
from ..models import Signal
from .base import Provider, normalise_region

SESSION = "keypulse-wei"
BASE = "https://weixin.sogou.com/weixin?type=2&query="

READ_SCRIPT = r"""
(() => {
  const t = (document.body.innerText || '').replace(/\s+/g, ' ');
  if (/antispider|验证码/.test(t)) return JSON.stringify({ error: '搜狗反爬验证' });
  const m = t.match(/找到约([\d,]+)条/);
  if (!m) return JSON.stringify({ error: 'no count on page' });
  return JSON.stringify({ count: Number(m[1].replace(/,/g, '')) });
})()
"""


class SogouWeChatBrowser(Provider):
    name = "sogou_wechat"
    label = "微信文章数(搜狗)"
    metric = "result_count"
    unit = "hits"
    kind = "pages"
    tier = "corpus"
    access = "browser"
    regions = ("CN", "GLOBAL")

    def check(self) -> tuple[bool, str]:
        try:
            if any(self.settings.cache_dir.glob(f"replay-{self.name}-*.json")):
                return True, "ok (replay)"
        except OSError:
            pass
        ok, reason = bridge.available()
        if not ok:
            return False, f"需要浏览器桥接：{reason}"
        return True, "ok (browser bridge)"

    def signal(self, keyword: str, region: str = "GLOBAL") -> Signal:
        recorded = self._replay(keyword)
        if recorded:
            return self._replayed_signal(keyword, region, recorded)
        url = BASE + urllib.parse.quote(keyword)
        last_error = "unknown"
        # Sogou occasionally serves an anti-spider page for one navigation and
        # then works; one retry with a fresh tab recovers most of those.
        for attempt in range(2):
            try:
                bridge.open_page(self.settings, url, session=SESSION)
                payload = bridge.eval_js(self.settings, READ_SCRIPT, session=SESSION)
                data = json.loads(payload) if isinstance(payload, str) else payload
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"
                continue
            if "error" in data:
                last_error = str(data["error"])
                continue
            return Signal(
                keyword=keyword,
                provider=self.name,
                metric=self.metric,
                value=float(data["count"]),
                unit=self.unit,
                region=normalise_region(region),
                detail={"vertical": "wechat_articles", "transport": "opencli"},
            )
        return self._failure(keyword, last_error, region)
