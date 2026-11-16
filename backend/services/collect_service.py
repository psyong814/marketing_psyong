"""1단계: 모든 수집기를 실행하고 결과를 DB 에 저장."""

from datetime import datetime

from backend.collectors import ALL_COLLECTORS
from backend.storage.db import DB


def _build(settings: dict):
    timeout = settings.get("http", {}).get("timeout", 10)
    conf = settings.get("collectors", {})
    return [cls(conf.get(cls.name, {}), timeout) for cls in ALL_COLLECTORS]


def collector_status(settings: dict) -> list[dict]:
    """수집기별 실행 가능 여부 (프론트의 상태 배지용)."""
    result = []
    for c in _build(settings):
        reason = c.skip_reason()
        result.append(
            {
                "name": c.name,
                "label": c.label,
                "ready": reason is None,
                "mock": c.is_mock(),
                "message": reason or ("Mock 데이터 사용 (API 키 없음)" if c.is_mock() else "준비됨"),
            }
        )
    return result


def run_collect(settings: dict, db: DB) -> dict:
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    sources, collected = [], []

    for c in _build(settings):
        info = {"name": c.name, "label": c.label, "mock": c.is_mock(), "count": 0}
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
        sources.append({**info, "status": "ok", "count": len(trends), "message": "완료"})

    db.save_run(run_id, sources, collected)
    return db.latest_run()
