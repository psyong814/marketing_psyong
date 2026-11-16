import json

from backend.affiliates.coupang_client import CoupangClient
from backend.settings import ROOT, env

from .base import Collector, Trend

MOCK_FILE = ROOT / "fixtures" / "coupang_goldbox_mock.json"


class CoupangGoldboxCollector(Collector):
    """쿠팡 골드박스(오늘의 특가). 키가 없으면 fixtures 의 Mock 데이터를 사용."""

    name = "coupang_goldbox"
    label = "쿠팡 골드박스"

    def is_mock(self) -> bool:
        return not (env("COUPANG_ACCESS_KEY") and env("COUPANG_SECRET_KEY"))

    def _load(self) -> list[dict]:
        if self.is_mock():
            return json.loads(MOCK_FILE.read_text(encoding="utf-8"))
        client = CoupangClient(env("COUPANG_ACCESS_KEY"), env("COUPANG_SECRET_KEY"), timeout=self.timeout)
        return client.goldbox(self.conf.get("sub_id"))

    def fetch(self) -> list[Trend]:
        products = self._load()
        mock = self.is_mock()
        limit = int(self.conf.get("limit", 20))
        return [
            Trend(
                source=self.name,
                keyword=p.get("productName", ""),
                rank=rank,
                extra={
                    "mock": mock,
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
