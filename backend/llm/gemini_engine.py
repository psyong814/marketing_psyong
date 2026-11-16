"""Gemini 로 트렌드 키워드의 '상품성'을 판별. 무료 한도를 아끼려고 모든 키워드를 한 번에 보낸다."""

import json

from .base import Judgement, LLMError
from .gemini_client import GeminiClient

PROMPT = """너는 한국의 제휴 마케팅(쿠팡 파트너스 등) 담당자다.
아래는 지금 한국에서 검색량이 급상승한 키워드와 관련 뉴스 제목이다.
각 키워드가 '자연스럽게 상품 구매로 이어질 수 있는지' 판단해라.

판단 규칙:
- 사건·사고, 사망, 범죄, 재난 피해, 정치, 특정 인물(연예인·정치인·일반인) 자체는 commercial=false.
  비극이나 논란을 상품 홍보에 이용하지 않는다.
- 날씨·계절·행사·취미·생활 이슈처럼 관련 상품이 분명하면 commercial=true. 예: 폭설 → 제설삽, 방한용품.
- 연예인·드라마·스포츠 경기 이름은 상품과의 연결이 억지스러우면 false.
- 의약품, 건강기능식품 효능, 금융상품, 성인용품, 무기는 false.
- score: 0~100 정수. 지금 이 키워드로 상품을 소개했을 때 구매로 이어질 가능성.
- category: 쇼핑몰 카테고리 이름(예: 생활용품, 가전디지털, 식품, 패션의류, 뷰티, 스포츠/레저,
  출산/유아동, 완구/취미, 주방용품, 반려동물용품, 문구/오피스). false 이면 빈 문자열.
- search_queries: 쇼핑몰 검색창에 넣을 구체적인 상품 검색어 1~3개. false 이면 빈 배열.
- reason: 판단 이유를 한국어 한 문장으로.
- keyword 는 입력 값을 그대로 돌려주고, 입력한 모든 키워드에 대해 하나씩 답한다.

키워드 목록(JSON):
"""

SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "keyword": {"type": "STRING"},
            "commercial": {"type": "BOOLEAN"},
            "score": {"type": "INTEGER"},
            "category": {"type": "STRING"},
            "search_queries": {"type": "ARRAY", "items": {"type": "STRING"}},
            "reason": {"type": "STRING"},
        },
        "required": ["keyword", "commercial", "score", "category", "search_queries", "reason"],
    },
}


class GeminiEngine:
    name = "gemini"

    def __init__(self, api_key: str, model: str, timeout: float = 60):
        self.client = GeminiClient(api_key, model, timeout)
        self.model = self.client.model

    def judge_trends(self, items: list[dict]) -> list[Judgement]:
        """items: [{"keyword": str, "news": [str, ...]}] → 입력 순서대로 Judgement. 응답에 빠진 키워드는 결과에서 제외."""
        if not items:
            return []
        payload = [{"keyword": i["keyword"], "news": i.get("news", [])[:2]} for i in items]
        raw = self.client.generate_json(PROMPT + json.dumps(payload, ensure_ascii=False), SCHEMA)
        if not isinstance(raw, list):
            raise LLMError(f"Gemini 응답 형식 오류: 배열이 아님 ({type(raw).__name__})")

        by_kw = {str(r.get("keyword", "")).strip(): r for r in raw if isinstance(r, dict)}
        results = []
        for item in items:
            r = by_kw.get(item["keyword"].strip())
            if not r:
                continue
            commercial = bool(r.get("commercial"))
            results.append(
                Judgement(
                    keyword=item["keyword"],
                    commercial=commercial,
                    score=max(0, min(100, int(r.get("score") or 0))),
                    category=str(r.get("category") or "") if commercial else "",
                    search_queries=[str(q) for q in (r.get("search_queries") or [])][:3] if commercial else [],
                    reason=str(r.get("reason") or ""),
                    engine=f"{self.name}:{self.model}",
                )
            )
        return results
