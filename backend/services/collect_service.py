"""1단계: 모든 수집기를 실행하고 결과를 DB 에 저장."""

from datetime import datetime

from backend.collectors import ALL_COLLECTORS
from backend.storage.db import DB


def _build(settings: dict, db: DB):
    """settings.yaml 에서 enabled: false 인 수집기는 아예 빼고(화면에도 안 보임) 만든다."""
    timeout = settings.get("http", {}).get("timeout", 10)
    conf = settings.get("collectors", {})
    return [
        cls(conf.get(cls.name, {}), timeout, db)
        for cls in ALL_COLLECTORS
        if conf.get(cls.name, {}).get("enabled", True)
    ]


def collector_status(settings: dict, db: DB) -> list[dict]:
    """수집기별 실행 가능 여부 (프론트의 상태 배지용)."""
    result = []
    for c in _build(settings, db):
        reason = c.skip_reason()
        result.append(
            {
                "name": c.name,
                "label": c.label,
                "ready": reason is None,
                "message": reason or "준비됨",
            }
        )
    return result


def run_collect(settings: dict, db: DB) -> dict:
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    sources, collected = [], []

    for c in _build(settings, db):
        info = {"name": c.name, "label": c.label, "count": 0}
        reason = c.skip_reason()
        if reason:
            sources.append({**info, "status": "skipped", "message": reason})
            continue
        try:
            trends = c.fetch()
        except Exception as e:  # 한 소스가 실패해도 나머지는 계속
            sources.append({**info, "status": "failed", "message": str(e)})
            continue
        collected.extend(trends)
        message = "완료"
        if not trends and c.name == "kakao_products":
            message = "저장된 상품이 없습니다. 🎁 카카오 상품에서 [실행]으로 수집하세요."
        sources.append({**info, "status": "ok", "count": len(trends), "message": message})

    db.save_run(run_id, sources, collected)
    return db.latest_run()
