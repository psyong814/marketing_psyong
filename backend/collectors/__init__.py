from .base import Collector, Trend
from .google_trends import GoogleTrendsCollector
from .kakao_products import KakaoProductsCollector
from .season_keywords import SeasonKeywordsCollector

# 쿠팡 골드박스(coupang_goldbox.py)는 API 승인 전까지 연결하지 않음. 승인 후 여기에 다시 추가.
ALL_COLLECTORS = [GoogleTrendsCollector, SeasonKeywordsCollector, KakaoProductsCollector]

__all__ = ["Collector", "Trend", "ALL_COLLECTORS"]
