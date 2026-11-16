"""쿠팡 골드박스(오늘의 특가) 수집기 — 현재 미사용.

쿠팡 파트너스 API 승인 후 collectors/__init__.py 의 ALL_COLLECTORS 에 다시 추가하고,
설정 화면에 쿠팡 키 입력 항목(env_store.FIELDS)을 되살리면 된다.
"""

from backend.affiliates.coupang_client import CoupangClient
from backend.settings import env

from .base import Collector, Trend


class CoupangGoldboxCollector(Collector):
    name = "coupang_goldbox"
    label = "쿠팡 골드박스"

    def skip_reason(self) -> str | None:
        reason = super().skip_reason()
        if reason:
            return reason
        if not (env("COUPANG_ACCESS_KEY") and env("COUPANG_SECRET_KEY")):
            return "쿠팡 파트너스 API 키 없음"
        return None

    def fetch(self) -> list[Trend]:
        client = CoupangClient(env("COUPANG_ACCESS_KEY"), env("COUPANG_SECRET_KEY"), timeout=self.timeout)
        products = client.goldbox(self.conf.get("sub_id"))
        limit = int(self.conf.get("limit", 20))
        return [
            Trend(
                source=self.name,
                keyword=p.get("productName", ""),
                rank=rank,
                extra={
                    "product_id": p.get("productId"),
                    "price": p.get("productPrice"),
                    "category": p.get("categoryName"),
                    "image": p.get("productImage"),
                    "url": p.get("productUrl"),
                    "is_rocket": p.get("isRocket"),
                },
            )
            for rank, p in enumerate(products[:limit], start=1)
        ]
