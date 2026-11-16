"""Google AI Studio (Gemini API) REST 클라이언트. 별도 SDK 없이 httpx 로 호출."""

import json
import time

import httpx

from .base import LLMError, LLMQuotaError

API_BASE = "https://generativelanguage.googleapis.com/v1beta"


def _error_message(resp: httpx.Response) -> str:
    try:
        return resp.json().get("error", {}).get("message") or resp.text[:300]
    except ValueError:
        return resp.text[:300]


class GeminiClient:
    def __init__(self, api_key: str, model: str, timeout: float = 60):
        self.api_key = api_key
        self.model = model.removeprefix("models/")
        self.timeout = timeout

    def _headers(self) -> dict:
        return {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}

    def generate_json(self, prompt: str, schema: dict, temperature: float = 0.2):
        """프롬프트를 보내고 JSON 스키마에 맞는 응답을 파싱해 돌려준다."""
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
                "responseSchema": schema,
            },
        }
        # 500/503(일시적 과부하)은 잠깐 기다렸다 한 번 더 시도
        for attempt in (1, 2):
            resp = httpx.post(
                f"{API_BASE}/models/{self.model}:generateContent",
                json=body,
                headers=self._headers(),
                timeout=self.timeout,
            )
            if resp.status_code not in (500, 503) or attempt == 2:
                break
            time.sleep(3)
        if resp.status_code == 429:
            raise LLMQuotaError(f"Gemini 무료 한도 초과: {_error_message(resp)}")
        if resp.status_code != 200:
            raise LLMError(f"Gemini 오류 {resp.status_code}: {_error_message(resp)}")

        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMError(f"Gemini 응답이 비어 있음: {data.get('promptFeedback')}")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"Gemini 응답 JSON 파싱 실패: {e}: {text[:200]}") from e

    def list_models(self) -> list[dict]:
        """generateContent 를 지원하는 모델 목록."""
        models, token = [], None
        while True:
            params = {"pageSize": 100}
            if token:
                params["pageToken"] = token
            resp = httpx.get(f"{API_BASE}/models", params=params, headers=self._headers(), timeout=self.timeout)
            if resp.status_code != 200:
                raise LLMError(f"Gemini 모델 목록 오류 {resp.status_code}: {_error_message(resp)}")
            data = resp.json()
            for m in data.get("models", []):
                if "generateContent" in m.get("supportedGenerationMethods", []):
                    models.append({"name": m["name"].removeprefix("models/"), "display_name": m.get("displayName", "")})
            token = data.get("nextPageToken")
            if not token:
                return models
