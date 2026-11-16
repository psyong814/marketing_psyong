from datetime import date, timedelta

import httpx

from backend.settings import env

from .base import Collector, Trend

API_URL = "https://openapi.naver.com/v1/datalab/shopping/categories"
MAX_PER_REQUEST = 3  # 쇼핑인사이트 API 는 요청당 카테고리 최대 3개


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


class NaverDatalabCollector(Collector):
    """네이버 데이터랩 쇼핑인사이트: 카테고리별 클릭 추이에서 '최근 7일 / 직전 7일' 상승률을 계산.

    키워드가 아니라 '뜨는 쇼핑 카테고리'를 돌려주며, 2단계 상품성 점수에 가중치로 쓴다.
    """

    name = "naver_datalab"
    label = "네이버 데이터랩"

    def skip_reason(self) -> str | None:
        reason = super().skip_reason()
        if reason:
            return reason
        if not (env("NAVER_CLIENT_ID") and env("NAVER_CLIENT_SECRET")):
            return ".env 에 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 없음"
        return None

    def fetch(self) -> list[Trend]:
        days = int(self.conf.get("days", 14))
        end = date.today() - timedelta(days=1)  # 당일 데이터는 불완전
        start = end - timedelta(days=days - 1)
        cats = list((self.conf.get("categories") or {}).items())
        headers = {
            "X-Naver-Client-Id": env("NAVER_CLIENT_ID"),
            "X-Naver-Client-Secret": env("NAVER_CLIENT_SECRET"),
            "Content-Type": "application/json",
        }

        rows = []
        for i in range(0, len(cats), MAX_PER_REQUEST):
            chunk = cats[i : i + MAX_PER_REQUEST]
            body = {
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "timeUnit": "date",
                "category": [{"name": name, "param": [str(cid)]} for cid, name in chunk],
            }
            resp = httpx.post(API_URL, json=body, headers=headers, timeout=self.timeout)
            if resp.status_code != 200:
                raise RuntimeError(f"데이터랩 API 오류 {resp.status_code}: {resp.text[:200]}")
            for result in resp.json().get("results", []):
                ratios = [d["ratio"] for d in result.get("data", [])]
                half = len(ratios) // 2
                prev, recent = _avg(ratios[:half]), _avg(ratios[half:])
                growth = (recent / prev - 1) * 100 if prev else 0.0
                rows.append((result["title"], result.get("category", []), growth, recent, prev))

        rows.sort(key=lambda r: r[2], reverse=True)
        return [
            Trend(
                source=self.name,
                keyword=title,
                rank=rank,
                score=round(growth, 2),
                extra={
                    "category_id": cid,
                    "growth_pct": round(growth, 2),
                    "recent_avg": round(recent, 2),
                    "prev_avg": round(prev, 2),
                },
            )
            for rank, (title, cid, growth, recent, prev) in enumerate(rows, start=1)
        ]
