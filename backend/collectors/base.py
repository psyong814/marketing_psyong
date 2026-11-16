from dataclasses import asdict, dataclass, field


@dataclass
class Trend:
    source: str
    keyword: str
    rank: int | None = None
    score: float | None = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class Collector:
    """모든 수집기의 공통 인터페이스."""

    name = "base"
    label = "base"

    def __init__(self, conf: dict, timeout: float):
        self.conf = conf or {}
        self.timeout = timeout

    def skip_reason(self) -> str | None:
        """실행할 수 없으면 이유를, 실행 가능하면 None 을 돌려준다."""
        if not self.conf.get("enabled", True):
            return "settings.yaml 에서 비활성화됨"
        return None

    def is_mock(self) -> bool:
        return False

    def fetch(self) -> list[Trend]:
        raise NotImplementedError
