from dataclasses import asdict, dataclass, field


@dataclass
class Judgement:
    """키워드 하나에 대한 판별 결과."""

    keyword: str
    commercial: bool
    score: int
    category: str = ""
    search_queries: list[str] = field(default_factory=list)
    reason: str = ""
    engine: str = "rule"

    def to_dict(self) -> dict:
        return asdict(self)


class LLMError(Exception):
    pass


class LLMQuotaError(LLMError):
    """무료 한도 초과(429). 호출하는 쪽에서 규칙 기반으로 대체한다."""
