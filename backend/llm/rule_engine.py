"""API 키 없이 동작하는 규칙 기반 판별기."""

from backend.rules import KeywordRules

from .base import Judgement


class RuleEngine:
    name = "rule"

    def __init__(self, rules: KeywordRules):
        self.rules = rules

    def judge_trends(self, items: list[dict]) -> list[Judgement]:
        """items: [{"keyword": str, "news": [str, ...]}]"""
        results = []
        for item in items:
            kw = item["keyword"]
            hint = self.rules.hint(kw)
            if hint:
                results.append(
                    Judgement(kw, True, 60, search_queries=[kw], reason=f"상품 단어 '{hint}' 포함", engine=self.name)
                )
            else:
                results.append(
                    Judgement(kw, False, 10, reason="상품과의 연관성을 찾지 못함 (규칙 기반)", engine=self.name)
                )
        return results
