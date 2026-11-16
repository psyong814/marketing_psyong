from datetime import date

import yaml

from backend.settings import ROOT

from .base import Collector, Trend

SEASON_FILE = ROOT / "config" / "season_keywords.yaml"


def _in_period(today: str, start: str, end: str) -> bool:
    """MM-DD 문자열 비교. start > end 이면 연말→연초로 넘어가는 기간."""
    if start <= end:
        return start <= today <= end
    return today >= start or today <= end


def active_season_keywords(today: date | None = None) -> list[dict]:
    """오늘 날짜에 해당하는 시즌 키워드 (가중치 높은 순, 같은 키워드는 가중치 높은 시즌 하나만).

    1단계 수집, 1단계 카카오 상품 정렬, 카카오 자동 수집의 '시즌 상품 먼저'에서 함께 쓴다.
    """
    if not SEASON_FILE.exists():
        return []
    data = yaml.safe_load(SEASON_FILE.read_text(encoding="utf-8")) or {}
    md = (today or date.today()).strftime("%m-%d")
    found: dict[str, dict] = {}
    for season in data.get("seasons", []):
        start, end = str(season["start"]), str(season["end"])
        if not _in_period(md, start, end):
            continue
        weight = float(season.get("weight", 70))
        for kw in season.get("keywords", []):
            kw = str(kw).strip()
            if kw and (kw not in found or found[kw]["weight"] < weight):
                found[kw] = {"keyword": kw, "weight": weight, "season": season.get("name", ""),
                             "period": f"{start}~{end}"}
    return sorted(found.values(), key=lambda s: s["weight"], reverse=True)


class SeasonKeywordsCollector(Collector):
    """config/season_keywords.yaml 에서 오늘 날짜에 해당하는 시즌 키워드를 꺼낸다. (API 불필요)"""

    name = "season_keywords"
    label = "시즌 키워드"

    def skip_reason(self) -> str | None:
        reason = super().skip_reason()
        if reason:
            return reason
        if not SEASON_FILE.exists():
            return "config/season_keywords.yaml 없음"
        return None

    def fetch(self) -> list[Trend]:
        limit = int(self.conf.get("limit", 30))
        return [
            Trend(
                source=self.name,
                keyword=s["keyword"],
                rank=rank,
                score=s["weight"],
                extra={"season": s["season"], "period": s["period"]},
            )
            for rank, s in enumerate(active_season_keywords()[:limit], start=1)
        ]
