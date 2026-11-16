from .base import Collector, Trend
from .coupang_goldbox import CoupangGoldboxCollector
from .google_trends import GoogleTrendsCollector
from .naver_datalab import NaverDatalabCollector

ALL_COLLECTORS = [GoogleTrendsCollector, NaverDatalabCollector, CoupangGoldboxCollector]

__all__ = ["Collector", "Trend", "ALL_COLLECTORS"]
