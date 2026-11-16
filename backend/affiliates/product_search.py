"""상품 검색: DB 에 저장된 카카오 선물하기 큐레이터 상품(실제 제휴 링크)에서 찾는다.

(쿠팡 검색은 API 승인 후 coupang_client.CoupangClient.search 로 다시 연결할 예정)
"""


class KakaoProductSearch:
    """매칭 규칙 (둘 중 하나):
      1) 상품에 붙인 태그가 검색어 안에 들어 있음      예) 태그 '수능' ↔ 검색어 '수능 선물'
      2) 검색어의 모든 단어가 상품명·브랜드에 들어 있음  예) '감귤 선물' ↔ '제주 감귤 선물세트'
    """

    def __init__(self, products: list[dict]):
        self.products = [p for p in products if p.get("active") and p.get("link")]

    @staticmethod
    def _tags(p: dict) -> list[str]:
        return [t.strip() for t in (p.get("tags") or "").split(",") if len(t.strip()) >= 2]

    @classmethod
    def match_score(cls, query: str, name: str, brand: str = "", tags: list[str] | None = None) -> int:
        """0 이면 불일치. 태그 일치는 3점, 상품명·브랜드에 들어 있는 단어 하나당 1점.
        카카오 자동 수집(링크 생성 전 상품 카드)에서도 같은 규칙을 쓰려고 분리."""
        q = query.strip()
        tokens = [t for t in q.replace(",", " ").split() if len(t) >= 2]
        if not tokens:
            return 0
        text = f"{brand or ''} {name or ''}"
        tag_hits = sum(1 for t in (tags or []) if t in q or q in t)
        word_hits = sum(1 for t in tokens if t in text)
        if tag_hits or word_hits == len(tokens):
            return tag_hits * 3 + word_hits
        return 0

    def find(self, query: str, limit: int = 3) -> list[dict]:
        """DB 행 그대로 돌려줌"""
        scored = []
        for p in self.products:
            s = self.match_score(query, p.get("name") or "", p.get("brand") or "", self._tags(p))
            if s:
                scored.append((s, p))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in scored[:limit]]

    def search(self, query: str, limit: int = 3) -> list[dict]:
        return [self.normalize(p) for p in self.find(query, limit)]

    @staticmethod
    def normalize(p: dict) -> dict:
        name = p.get("name") or ""
        brand = p.get("brand") or ""
        return {
            "platform": "kakao",
            "product_id": p.get("product_id") or p.get("product_key"),
            "name": f"[{brand}] {name}" if brand and brand not in name else name,
            "price": p.get("benefit") or p.get("price"),
            "image": p.get("image") or "",
            "url": p.get("link") or "",
            "is_rocket": False,
            "category": "",
            "mock": False,
        }
