"""설정 화면: 저장된 키로 각 서비스에 실제로 연결되는지 확인."""

from backend.env_store import EnvStoreError
from backend.llm import gemini_model
from backend.llm.gemini_client import GeminiClient
from backend.settings import env


def test_connection(service: str, settings: dict) -> dict:
    timeout = float(settings.get("http", {}).get("timeout", 10))

    if service == "gemini":
        key = env("GEMINI_API_KEY")
        if not key:
            return {"ok": False, "message": "API 키가 저장되어 있지 않습니다."}
        model = gemini_model(settings)
        client = GeminiClient(key, model, timeout=max(timeout, 20))
        try:
            models = client.list_models()
        except Exception as e:
            return {"ok": False, "message": str(e)}
        names = [m["name"] for m in models]
        # '-latest' 별칭은 목록에 없을 수도 있으므로 경고만 한다
        known = model in names or model.endswith("-latest")
        msg = f"연결 성공 · 사용 가능 모델 {len(names)}개"
        if not known:
            msg += f" · 현재 모델 '{model}' 이 목록에 없습니다. 모델을 다시 골라 주세요."
        return {"ok": True, "message": msg, "model": model, "models": models}

    raise EnvStoreError(f"알 수 없는 서비스: {service}")
