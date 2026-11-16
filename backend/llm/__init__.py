"""LLM 엔진 선택. settings.yaml 의 llm.provider 로 rule / gemini 를 고른다."""

from backend.rules import KeywordRules
from backend.settings import env

from .base import Judgement, LLMError, LLMQuotaError
from .gemini_engine import GeminiEngine
from .rule_engine import RuleEngine


def resolve_provider(settings: dict) -> str:
    provider = (settings.get("llm", {}).get("provider") or "auto").lower()
    if provider == "auto":
        return "gemini" if env("GEMINI_API_KEY") else "rule"
    return provider


def gemini_model(settings: dict) -> str:
    """설정 화면에서 고른 모델(GEMINI_MODEL)이 있으면 우선, 없으면 settings.yaml 값."""
    conf = settings.get("llm", {}).get("gemini", {})
    return env("GEMINI_MODEL") or conf.get("model") or "gemini-flash-lite-latest"


def get_engine(settings: dict, rules: KeywordRules):
    provider = resolve_provider(settings)
    if provider == "gemini":
        conf = settings.get("llm", {}).get("gemini", {})
        key = env("GEMINI_API_KEY")
        if not key:
            raise LLMError("llm.provider 가 gemini 인데 Gemini API 키가 없습니다. 설정 화면에서 입력하세요.")
        return GeminiEngine(key, gemini_model(settings), float(conf.get("timeout", 60)))
    return RuleEngine(rules)


def engine_status(settings: dict) -> dict:
    provider = resolve_provider(settings)
    return {
        "provider": provider,
        "label": "Gemini" if provider == "gemini" else "규칙 기반",
        "model": gemini_model(settings) if provider == "gemini" else None,
        "gemini_key_set": bool(env("GEMINI_API_KEY")),
    }


__all__ = ["Judgement", "LLMError", "LLMQuotaError", "get_engine", "engine_status", "resolve_provider", "gemini_model"]
