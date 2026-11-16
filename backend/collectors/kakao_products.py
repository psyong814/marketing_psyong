import re

from backend.affiliates.product_search import KakaoProductSearch

from .base import Collector, Trend
from .season_keywords import active_season_keywords


def _buyers(badge: str) -> int:
    """'최근 구매 41명' → 41"""
    m = re.search(r"([\d,]+)\s*명", badge or "")
    return int(m.group(1).replace(",", "")) if m else 0


class KakaoProductsCollector(Collector):
    """🎁 카카오 상품 화면에서 수집해 DB(kakao_products)에 저장된 큐레이터 상품.

    외부 API 호출 없이 DB 에서 읽는다. 실제 제휴 링크가 있는 '사용' 상품만.
    순서: ① 오늘 시즌 키워드와 맞는 상품 (시즌 가중치 → 최근 구매자 수 순)
          ② 남는 자리는 나머지 상품을 최근 구매자 수 순으로 채움
    """

    name = "kakao_products"
    label = "카카오 선물하기"

    def skip_reason(self) -> str | None:
        reason = super().skip_reason()
        if reason:
            return reason
        if self.db is None:
            return "DB 연결 없음"
        return None

    def fetch(self) -> list[Trend]:
        limit = int(self.conf.get("limit", 30))
        search = KakaoProductSearch(self.db.list_kakao_products(active_only=True))
        if not search.products:
            return []

        by_buyers = sorted(search.products, key=lambda p: (_buyers(p.get("badge")), p.get("id") or 0), reverse=True)
        picked: list[tuple[dict, dict | None]] = []   # (상품, 맞은 시즌 키워드)
        used: set[int] = set()

        if self.conf.get("season_first", True):
            for s in active_season_keywords():
                matched = [p for p in by_buyers if p["id"] not in used
                           and search.match_score(s["keyword"], p.get("name") or "", p.get("brand") or "",
                                                  search._tags(p))]
                for p in matched:
                    used.add(p["id"])
                    picked.append((p, s))
        for p in by_buyers:
            if p["id"] not in used:
                used.add(p["id"])
                picked.append((p, None))

        trends = []
        for rank, (p, season) in enumerate(picked[:limit], start=1):
            buyers = _buyers(p.get("badge"))
            trends.append(
                Trend(
                    source=self.name,
                    keyword=p["name"],
                    rank=rank,
                    score=float(buyers),
                    extra={
                        "kakao_id": p["id"],
                        "product_id": p.get("product_id") or p.get("product_key"),
                        "brand": p.get("brand") or "",
                        "price": p.get("price"),
                        "benefit": p.get("benefit"),
                        "discount": p.get("discount") or "",
                        "badge": p.get("badge") or "",
                        "buyers": buyers,
                        "image": p.get("image") or "",
                        "url": p.get("link") or "",
                        "product_url": p.get("product_url") or "",
                        "tags": p.get("tags") or "",
                        "season_keyword": season["keyword"] if season else "",
                        "season": season["season"] if season else "",
                        "season_weight": season["weight"] if season else 0,
                    },
                )
            )
        return trends
