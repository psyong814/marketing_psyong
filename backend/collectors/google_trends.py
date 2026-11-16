import re
import xml.etree.ElementTree as ET

import httpx

from .base import Collector, Trend

RSS_URL = "https://trends.google.com/trending/rss"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(el: ET.Element, name: str) -> str | None:
    for c in el:
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return None


def _parse_traffic(text: str | None) -> float | None:
    """'2,000+' / '20K+' 같은 값을 숫자로."""
    if not text:
        return None
    m = re.match(r"([\d,.]+)\s*([KkMm만천]?)", text)
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    mult = {"k": 1e3, "m": 1e6, "만": 1e4, "천": 1e3}.get(m.group(2).lower(), 1)
    return num * mult


class GoogleTrendsCollector(Collector):
    """구글 트렌드 '지금 뜨는 검색어' RSS (공식 공개 피드, 키 불필요)."""

    name = "google_trends"
    label = "구글 트렌드"

    def fetch(self) -> list[Trend]:
        geo = self.conf.get("geo", "KR")
        limit = int(self.conf.get("limit", 20))
        resp = httpx.get(
            RSS_URL,
            params={"geo": geo},
            timeout=self.timeout,
            headers={"User-Agent": "Mozilla/5.0 (marketing-bot)"},
            follow_redirects=True,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        trends: list[Trend] = []
        for rank, item in enumerate(root.iter("item"), start=1):
            title = _child_text(item, "title")
            if not title:
                continue
            news = [
                {"title": _child_text(c, "news_item_title"), "url": _child_text(c, "news_item_url")}
                for c in item
                if _local(c.tag) == "news_item"
            ]
            traffic = _child_text(item, "approx_traffic")
            trends.append(
                Trend(
                    source=self.name,
                    keyword=title,
                    rank=rank,
                    score=_parse_traffic(traffic),
                    extra={"traffic": traffic, "pub_date": _child_text(item, "pubDate"), "news": news[:3]},
                )
            )
            if len(trends) >= limit:
                break
        return trends
