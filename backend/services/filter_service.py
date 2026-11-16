"""2단계: 최근 수집 결과 → 중복 제거 → 금칙어 제외 → 상품성 판별(rule / gemini) → 카카오 상품 매칭 → DB 저장.

상품은 카카오 선물하기 큐레이터 상품(DB kakao_products, 실제 제휴 링크)만 사용한다.
(쿠팡은 API 승인 후 backend/affiliates 의 코드를 다시 연결할 예정)
"""

from backend.affiliates.product_search import KakaoProductSearch
from backend.llm import Judgement, LLMError, LLMQuotaError, get_engine
from backend.llm.rule_engine import RuleEngine
from backend.rules import load_rules
from backend.storage.db import DB

# 같은 키워드가 여러 소스에 있으면 앞쪽 소스를 남긴다.
SOURCE_PRIORITY = {"kakao_products": 0, "season_keywords": 1, "google_trends": 2}


class UserError(Exception):
    """사용자가 순서를 잘못 밟은 경우 (HTTP 400)."""

    code = 400


def _dedupe(trends: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for t in sorted(trends, key=lambda t: (SOURCE_PRIORITY.get(t["source"], 9), t["rank"] or 0)):
        key = t["keyword"].replace(" ", "").lower()
        seen.setdefault(key, t)
    return list(seen.values())


def _kakao_product(t: dict) -> dict:
    """1단계 '카카오 선물하기' 수집 항목 → 상품 (그 상품 자체)"""
    x = t.get("extra", {})
    brand = x.get("brand") or ""
    name = t["keyword"]
    return {
        "platform": "kakao",
        "product_id": x.get("product_id"),
        "name": f"[{brand}] {name}" if brand and brand not in name else name,
        "price": x.get("benefit") or x.get("price"),
        "image": x.get("image") or "",
        "url": x.get("url") or "",
        "is_rocket": False,
        "category": "",
        "query": "카카오 선물하기",
        "mock": False,
    }


def _judge_trends(engine, items: list[dict], db: DB, cache_days: int, rules, warnings: list[str]) -> dict[str, Judgement]:
    """구글 트렌드 키워드 판별. Gemini 는 캐시를 먼저 보고, 실패하면 규칙 기반으로 대체."""
    result: dict[str, Judgement] = {}
    use_cache = engine.name == "gemini"
    cache_key = f"gemini:{getattr(engine, 'model', '')}"

    todo = []
    for item in items:
        cached = db.get_cached(item["keyword"], cache_key, cache_days) if use_cache else None
        if cached:
            j = Judgement(**cached)
            j.reason = j.reason + " (캐시)"
            result[item["keyword"]] = j
        else:
            todo.append(item)

    if todo:
        try:
            judged = engine.judge_trends(todo)
            if use_cache:
                db.set_cached([(j.keyword, cache_key, j.to_dict()) for j in judged])
            result.update({j.keyword: j for j in judged})
        except (LLMQuotaError, LLMError) as e:
            warnings.append(f"{e} → 규칙 기반으로 대체했습니다.")

    missing = [i for i in todo if i["keyword"] not in result]
    if missing:
        if engine.name == "gemini" and len(missing) < len(todo):
            warnings.append(f"Gemini 응답에서 {len(missing)}개 키워드가 빠져 규칙 기반으로 판별했습니다.")
        for j in RuleEngine(rules).judge_trends(missing):
            result[j.keyword] = j
    return result


def run_filter(settings: dict, db: DB) -> dict:
    run = db.latest_run()
    if not run or not run["trends"]:
        raise UserError("분석할 수집 결과가 없습니다. 먼저 1단계에서 '지금 수집'을 실행하세요.")

    conf = settings.get("filter", {})
    min_score = int(conf.get("min_score", 50))
    per_keyword = int(conf.get("products_per_keyword", 3))
    cache_days = int(settings.get("llm", {}).get("gemini", {}).get("cache_days", 7))
    rules = load_rules()
    warnings: list[str] = []

    try:
        engine = get_engine(settings, rules)
    except LLMError as e:
        warnings.append(f"{e} → 규칙 기반으로 진행합니다.")
        engine = RuleEngine(rules)
    engine_label = f"gemini:{engine.model}" if engine.name == "gemini" else "rule"

    trends = _dedupe(run["trends"])
    rows: list[dict] = []
    pending: list[dict] = []

    # 1) 금칙어 · 소스별 기본 판별
    for t in trends:
        kw, src, x = t["keyword"], t["source"], t.get("extra", {})
        news_titles = [n.get("title") or "" for n in x.get("news", [])]
        bad = rules.blacklisted(kw)
        bad_news = None if bad else rules.blacklisted(" ".join(news_titles))
        if bad or bad_news:
            reason = f"금칙어 '{bad}' 포함" if bad else f"관련 뉴스에 금칙어 '{bad_news}' 포함"
            rows.append({"trend": t, "j": Judgement(kw, False, 0, reason=reason, engine="blacklist")})
        elif src == "season_keywords":
            j = Judgement(kw, True, int(t.get("score") or 70), search_queries=[kw],
                          reason=f"시즌 키워드 ({x.get('season', '')} {x.get('period', '')})", engine="season")
            rows.append({"trend": t, "j": j})
        elif src == "kakao_products":
            # 실제 판매 반응(최근 구매자 수)이 있을수록 점수를 조금 올림: 70 ~ 85
            buyers = int(x.get("buyers") or 0)
            score = 70 + min(15, buyers // 5)
            reason = "카카오 선물하기 큐레이터 상품" + (f" ({x.get('badge')})" if x.get("badge") else "")
            if x.get("season_keyword"):
                # 오늘 시즌 키워드와 맞는 상품은 그 시즌 가중치만큼은 점수를 보장
                score = max(score, int(x.get("season_weight") or 0))
                reason += f" · 시즌 '{x['season_keyword']}' ({x.get('season', '')})"
            if x.get("tags"):
                reason += f" · 태그: {x['tags']}"
            rows.append({"trend": t, "j": Judgement(kw, True, score, reason=reason, engine="kakao")})
        elif src == "google_trends":
            pending.append(t)
        # 그 밖의 소스(예전 수집 회차의 쿠팡 골드박스 등)는 무시

    # 2) 구글 트렌드 키워드는 LLM(또는 규칙)으로 판별 — 한 번에 묶어서 호출
    items = [{"keyword": t["keyword"], "news": [n.get("title") or "" for n in t.get("extra", {}).get("news", [])]}
             for t in pending]
    judged = _judge_trends(engine, items, db, cache_days, rules, warnings)
    for t in pending:
        rows.append({"trend": t, "j": judged[t["keyword"]]})

    # 3) 카카오 상품 매칭
    #    - '카카오 선물하기' 항목은 그 상품 자체를 붙임
    #    - 시즌·트렌드 키워드는 DB 의 카카오 상품을 이름·브랜드·태그로 검색 (외부 호출 없음)
    passed_rows = [r for r in rows if r["j"].commercial and r["j"].score >= min_score]
    kakao = KakaoProductSearch(db.list_kakao_products(active_only=True))
    for r in passed_rows:
        if r["trend"]["source"] == "kakao_products":
            r["products"] = [_kakao_product(r["trend"])]
            continue
        seen, found = set(), []
        for q in [*r["j"].search_queries, r["j"].keyword]:
            for p in kakao.search(q, per_keyword):
                if p["product_id"] not in seen and len(found) < per_keyword:
                    seen.add(p["product_id"])
                    found.append({**p, "query": q})
        r["products"] = found

    # 4) 저장
    keywords = [
        {**r["j"].to_dict(), "source": r["trend"]["source"], "products": r.get("products", [])}
        for r in rows
    ]
    passed = [k for k in keywords if k["commercial"] and k["score"] >= min_score]
    summary = {
        "total": len(keywords),
        "passed": len(passed),
        "excluded": len(keywords) - len(passed),
        "matched_keywords": sum(1 for k in passed if k["products"]),
        "products": sum(len(k["products"]) for k in keywords),
        "min_score": min_score,
        "per_keyword": per_keyword,
        "kakao_products": len(kakao.products),
        "warnings": warnings,
    }
    db.save_analysis(run["run_id"], engine_label, summary, keywords)
    return db.latest_analysis()
