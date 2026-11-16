"""3단계: 2단계 분석 결과에서 홍보할 카카오 상품을 키워드별로 하나씩 골라 제휴 링크를 확정.

- 카카오 큐레이터 링크(gift-link.kakao.com/...)는 수집할 때 이미 만들어진 완성 링크라서
  새로 만들 필요는 없고, '어느 키워드에 어느 상품 링크를 쓸지' 고르고 확정하는 단계다.
- 링크가 살아 있는지 확인하려고 자동으로 접속하지는 않는다.
  (내 제휴 링크를 프로그램이 직접 누르면 클릭 수가 늘어 큐레이터 실적 통계가 왜곡될 수 있음)
  → 형식(도메인)만 확인한다.
"""

import json
import re

from backend.services.filter_service import UserError
from backend.storage.db import DB

LINK_RE = re.compile(r"^https://gift-link\.kakao\.com/\S+$")


def link_ok(url: str | None) -> bool:
    return bool(url and LINK_RE.match(url))


def _option(p: dict) -> dict:
    return {
        "product_id": str(p.get("product_id") or ""),
        "name": p.get("name") or "",
        "price": p.get("price"),
        "image": p.get("image") or "",
        "url": p.get("url") or "",
    }


def _apply_choice(row: dict, opt: dict, kakao_by_id: dict) -> dict:
    """선택한 상품 정보를 행에 채움 (카카오 공식 안내 문구·상품 페이지는 DB 에서)"""
    src = kakao_by_id.get(opt["product_id"], {})
    row.update({
        "product_id": opt["product_id"],
        "product_name": opt["name"],
        "price": opt["price"],
        "image": opt["image"],
        "product_url": src.get("product_url") or "",
        "link": opt["url"],
        "disclosure": src.get("disclosure") or "",
        "link_ok": int(link_ok(opt["url"])),
    })
    return row


def _kakao_index(db: DB) -> dict:
    return {str(p.get("product_id") or p["product_key"]): p for p in db.list_kakao_products()}


def build_links(settings: dict, db: DB) -> dict:
    """가장 최근 2단계 분석으로 링크 후보를 (다시) 만든다."""
    a = db.latest_analysis()
    if not a or not a.get("id"):
        raise UserError("2단계 분석 결과가 없습니다. 먼저 2단계에서 '필터·매칭 실행'을 눌러 주세요.")
    min_score = int((a.get("summary") or {}).get("min_score", 50))
    auto_select = int(settings.get("links", {}).get("auto_select", 10))
    kakao_by_id = _kakao_index(db)

    passed = [
        k for k in a["keywords"]
        if k["commercial"] and k["score"] >= min_score
        and any(p.get("platform") == "kakao" and p.get("url") for p in k["products"])
    ]
    # 시즌·트렌드 키워드에 먼저 상품을 배정하고(홍보 문구의 맥락이 됨),
    # 카카오 상품 자체 항목(키워드 = 상품명)은 남은 상품에만 쓴다. 같은 그룹 안에서는 점수 높은 순.
    passed.sort(key=lambda k: (k["source"] == "kakao_products", -k["score"]))

    rows, used, skipped = [], {}, []
    for k in passed:
        opts = [_option(p) for p in k["products"] if p.get("platform") == "kakao" and p.get("url")]
        # 같은 상품이 여러 키워드에 겹치면 점수 높은 키워드에 먼저 배정하고, 다음 키워드는 다른 후보를 씀
        choice = next((o for o in opts if o["product_id"] not in used), None)
        if choice is None:
            # 후보 상품이 모두 다른(점수 높은) 키워드에 이미 배정됨 → 같은 링크로 두 번 홍보하지 않도록 제외
            skipped.append({
                "keyword": k["keyword"],
                "score": k["score"],
                "product": opts[0]["name"],
                "taken_by": used[opts[0]["product_id"]],
            })
            continue
        used[choice["product_id"]] = k["keyword"]
        row = {
            "keyword": k["keyword"],
            "source": k["source"],
            "score": k["score"],
            "reason": k.get("reason") or "",
            "options": json.dumps(opts, ensure_ascii=False),
            "selected": 0,
        }
        rows.append(_apply_choice(row, choice, kakao_by_id))

    for r in rows[:auto_select]:
        r["selected"] = int(bool(r["link_ok"]))

    db.replace_links(a["id"], rows, skipped)
    return db.latest_links()


def update_link(db: DB, link_id: int, body: dict) -> dict:
    row = db.get_link(link_id)
    if not row:
        raise UserError("링크 후보를 찾을 수 없습니다.")
    fields = {}
    if "product_id" in body:
        opt = next((o for o in row["options"] if o["product_id"] == str(body["product_id"])), None)
        if opt is None:
            raise UserError("이 키워드의 후보 상품이 아닙니다.")
        fields.update(_apply_choice({}, opt, _kakao_index(db)))
    if "selected" in body:
        selected = bool(body["selected"])
        link = fields.get("link", row["link"])
        if selected and not link_ok(link):
            raise UserError("카카오 큐레이터 링크 형식(https://gift-link.kakao.com/...)이 아니라 확정할 수 없습니다.")
        fields["selected"] = int(selected)
    db.update_link(link_id, fields)
    return db.get_link(link_id)
