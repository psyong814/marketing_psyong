"""화면(⚙ 시스템 설정)에서 바꾸는 개수·기준값.

config/settings.yaml 은 주석을 살려두려고 직접 고치지 않고, 바꾼 값만 data/app_settings.json 에 저장해
서버가 시작될 때와 저장할 때 settings 에 덮어쓴다 (재시작 불필요).
"""

import json

from backend.settings import ROOT

OVERRIDE_FILE = ROOT / "data" / "app_settings.json"

# key 는 settings.yaml 의 경로 (점으로 구분)
FIELDS = [
    {"key": "collectors.google_trends.limit", "label": "구글 트렌드 최대 개수", "step": "1단계 트렌드 수집",
     "min": 1, "max": 50, "default": 20},
    {"key": "collectors.season_keywords.limit", "label": "시즌 키워드 최대 개수", "step": "1단계 트렌드 수집",
     "min": 1, "max": 200, "default": 30},
    {"key": "collectors.kakao_products.limit", "core": True, "label": "1단계에 넣을 카카오 상품 수", "step": "1단계 트렌드 수집",
     "min": 1, "max": 1000, "default": 30, "help": "시즌 상품 먼저, 나머지는 인기순"},
    {"key": "filter.min_score", "core": True, "label": "2단계 통과 기준 점수", "step": "2단계 필터·상품 매칭",
     "min": 0, "max": 100, "default": 50},
    {"key": "filter.products_per_keyword", "label": "키워드당 상품 후보 수", "step": "2단계 필터·상품 매칭",
     "min": 1, "max": 10, "default": 3},
    {"key": "links.auto_select", "core": True, "label": "3단계 미리 확정할 개수", "step": "3단계 제휴 링크 확정",
     "min": 0, "max": 1000, "default": 10},
]
_BY_KEY = {f["key"]: f for f in FIELDS}


class AppSettingsError(ValueError):
    code = 400


def _get(settings: dict, key: str):
    node = settings
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _set(settings: dict, key: str, value) -> None:
    *parents, last = key.split(".")
    node = settings
    for part in parents:
        node = node.setdefault(part, {})
    node[last] = value


def _load() -> dict:
    try:
        data = json.loads(OVERRIDE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def apply(settings: dict) -> None:
    """저장된 값을 settings 에 덮어쓴다 (서버 시작 시 1회)."""
    for key, value in _load().items():
        if key in _BY_KEY:
            _set(settings, key, value)


def public_view(settings: dict) -> dict:
    fields = []
    for f in FIELDS:
        value = _get(settings, f["key"])
        fields.append({**f, "value": f["default"] if value is None else value})
    return {"fields": fields}


def save(settings: dict, values: dict) -> dict:
    saved = _load()
    for key, raw in (values or {}).items():
        f = _BY_KEY.get(key)
        if f is None:
            raise AppSettingsError(f"바꿀 수 없는 항목입니다: {key}")
        try:
            value = int(raw)
        except (TypeError, ValueError) as e:
            raise AppSettingsError(f"{f['label']}: 숫자를 입력하세요.") from e
        if not f["min"] <= value <= f["max"]:
            raise AppSettingsError(f"{f['label']}: {f['min']}~{f['max']} 사이로 입력하세요.")
        saved[key] = value
        _set(settings, key, value)
    OVERRIDE_FILE.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDE_FILE.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    return public_view(settings)
