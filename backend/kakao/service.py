"""카카오 선물하기 수집 실행 관리 (earning_kakao/app.py 의 상태·로그·실행 로직 이식)

- 브라우저 작업은 KakaoBrowser 의 전용 스레드 1개에서만 실행 (Playwright sync API 제약)
- 수집된 상품은 바로 SQLite(kakao_products) 에 저장
"""

import html
import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

from backend.collectors.season_keywords import active_season_keywords
from backend.storage.db import DB

from . import settings_store
from .automation import KakaoBrowser, LoginError
from .collector import A11Y_LINE, NOISE_LINES, clean_brand
from .settings_store import SettingsError

URL_RE = re.compile(r"https?://\S+")
OG_TITLE_RE = re.compile(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"')


def is_suspect_name(name: str) -> bool:
    """예전 파서가 잘못 읽은 상품명 (안내 문구 / ', 브랜드명 : OOO')"""
    name = (name or "").strip()
    return not name or name in NOISE_LINES or name.startswith(",") or bool(A11Y_LINE.search(name))


def fetch_product_title(product_url: str, timeout: float = 15) -> str | None:
    """상품 페이지(공개)의 og:title 에서 상품명 읽기"""
    if not product_url.startswith("https://gift.kakao.com/product/"):
        return None
    try:
        r = httpx.get(product_url, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True, timeout=timeout)
        m = OG_TITLE_RE.search(r.text) if r.status_code == 200 else None
        return html.unescape(m.group(1)).strip() if m else None
    except httpx.HTTPError:
        return None


class KakaoBusy(Exception):
    code = 409


class KakaoService:
    def __init__(self, db: DB):
        self.db = db
        self.state = {
            "logging_in": False,   # [실행] 진행 중
            "running": False,      # [링크 수집] 단독 진행 중
            "logged_in": False,
            "step": "대기",
            "progress": 0,
            "collected": 0,
            "last_error": "",
        }
        self.logs: list[dict] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.browser = KakaoBrowser(self.log)

    # ---------- 상태 · 로그 ----------

    def log(self, msg: str, level: str = "info") -> None:
        with self._lock:
            self.logs.append({
                "id": len(self.logs) + 1,
                "time": datetime.now().strftime("%H:%M:%S"),
                "level": level,
                "msg": msg,
            })
            del self.logs[:-500]  # 최근 500줄만 유지
        print(f"[kakao:{level}] {msg}")

    def logs_since(self, since: int) -> list[dict]:
        with self._lock:
            return [l for l in self.logs if l["id"] > since]

    def status(self) -> dict:
        return {**self.state, "busy": self.busy(), "browser_open": self.browser.is_open}

    def busy(self) -> bool:
        return self.state["logging_in"] or self.state["running"]

    # ---------- 수집 결과 저장 ----------

    @staticmethod
    def _won(v) -> int | None:
        digits = re.sub(r"[^\d]", "", str(v or ""))
        return int(digits) if digits else None

    @classmethod
    def to_row(cls, item: dict) -> dict | None:
        """collector 결과(또는 results.json 항목) → kakao_products 행"""
        name = (item.get("name") or "").strip()
        key = (item.get("product_id") or "").strip() or name
        if not key or not name:
            return None
        copied = item.get("copied_text") or ""
        disclosure = "\n".join(l for l in copied.splitlines() if not URL_RE.search(l)).strip()
        return {
            "product_key": key,
            "product_id": item.get("product_id") or "",
            "brand": clean_brand(item.get("brand")),
            "name": name,
            "discount": item.get("discount") or "",
            "price": cls._won(item.get("price")),
            "benefit": cls._won(item.get("benefit")),
            "badge": item.get("badge") or "",
            "image": item.get("image") or "",
            "product_url": item.get("product_url") or "",
            "link": item.get("link") or "",
            "link_source": item.get("link_source") or "",
            "disclosure": disclosure,
            "collected_at": item.get("collected_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _on_item(self, max_items: int):
        def on_item(item: dict) -> None:
            if is_suspect_name(item.get("name")):
                title = fetch_product_title(item.get("product_url") or "")
                if title:
                    self.log(f"상품명을 상품 페이지에서 보정: {title[:30]}")
                    item = {**item, "name": title}
            row = self.to_row(item)
            if row:
                self.db.upsert_kakao_product(row)
                if item.get("season_keyword"):
                    # 시즌 키워드로 찾은 상품은 그 키워드를 태그로 붙여 2단계 매칭에 바로 쓰이게 함
                    self.db.add_kakao_tag(row["product_key"], item["season_keyword"])
            self.state["collected"] += 1
            n = self.state["collected"]
            self.state["progress"] = min(100, int(n / max(1, max_items) * 100))
            self.state["step"] = f"링크 수집 중 ({n}/{max_items})"
        return on_item

    # ---------- 시즌 상품 우선 ----------

    def _season(self, cfg: dict) -> dict | None:
        """'시즌 상품 먼저' 옵션이 켜져 있으면 수집기에 넘길 시즌 정보"""
        if not cfg.get("season_first"):
            return None
        keywords = [s["keyword"] for s in active_season_keywords()]
        if not keywords:
            self.log("오늘 해당하는 시즌 키워드가 없어 순서대로 수집합니다.")
            return None
        method = cfg.get("season_method") or "search"
        self.log(f"시즌 상품 먼저 수집 ({'검색창 사용' if method == 'search' else '목록 훑기'}): "
                 f"{', '.join(keywords[:8])}{' 외' if len(keywords) > 8 else ''}")
        return {"keywords": keywords, "method": method, "per_search": cfg.get("search_per_keyword") or 5}

    # ---------- 실행 ----------

    def _done(self, flag: str, ok_step: str):
        def callback(future):
            try:
                ok = future.result()
                self.state.update(step=ok_step, last_error="")
                if flag == "logging_in":
                    self.state["logged_in"] = bool(ok)
            except LoginError as e:
                self.log(f"실패: {e}", "error")
                self.state.update(logged_in=False, step="실패", last_error=str(e))
            except Exception as e:  # 브라우저 실행 실패 등
                self.log(f"오류: {type(e).__name__}: {e}", "error")
                self.state.update(step="오류", last_error=str(e))
            finally:
                self.state[flag] = False
        return callback

    def run(self) -> None:
        """로그인 → 큐레이터 → 모바일 전환 → (설정 시) 링크 수집"""
        if self.busy():
            raise KakaoBusy("다른 작업이 진행 중입니다.")
        cfg = settings_store.load()
        user_id, password = settings_store.credentials()
        if not user_id or not password:
            raise SettingsError("⚙ 시스템 설정에서 카카오 아이디와 비밀번호를 먼저 저장하세요.")

        self.log(f"실행: 선물하기 열기 (계정: {user_id})")
        self._stop.clear()
        self.state.update(logging_in=True, step="사이트 여는 중", progress=0, collected=0, last_error="")
        self.browser.run(
            self.browser.run_all, user_id, password, cfg["headless"], cfg["login_timeout"],
            cfg["open_curator"], cfg["mobile_mode"], cfg["mobile_device"], cfg["mobile_method"],
            cfg["close_devtools"], cfg["auto_collect"], cfg["collect_max"], cfg["collect_interval"],
            self._on_item(cfg["collect_max"]), self._stop, self.db.kakao_product_keys(), self._season(cfg),
        ).add_done_callback(self._done("logging_in", "실행 완료"))

    def collect(self, max_items: int | None = None, interval: float | None = None) -> None:
        """이미 열린 큐레이터 화면에서 링크 수집만 다시 실행"""
        if self.busy():
            raise KakaoBusy("다른 작업이 진행 중입니다.")
        if not self.browser.is_open:
            raise SettingsError("브라우저가 열려 있지 않습니다. 먼저 [실행]을 눌러 주세요.")
        cfg = settings_store.load()
        max_items = int(max_items or cfg["collect_max"])
        interval = float(interval or cfg["collect_interval"])
        self._stop.clear()
        self.state.update(running=True, progress=0, collected=0, step="링크 수집 중", last_error="")
        self.browser.run(
            self.browser.collect_links, max_items, interval, self._stop,
            self._on_item(max_items), self.db.kakao_product_keys(), self._season(cfg),
        ).add_done_callback(self._done("running", "수집 완료"))

    def stop(self) -> None:
        self._stop.set()
        self.log("중지 요청 (현재 상품 처리 후 멈춥니다)", "warn")

    def close_browser(self) -> None:
        if self.busy():
            raise KakaoBusy("작업 중에는 닫을 수 없습니다.")
        self.browser.run(self.browser.close).result(timeout=30)
        self.state.update(logged_in=False, step="대기", progress=0)

    def clear_session(self) -> None:
        """브라우저에 저장된 카카오 로그인 상태(쿠키) 삭제"""
        if self.busy():
            raise KakaoBusy("작업 중에는 실행할 수 없습니다.")
        try:
            self.browser.run(self.browser.logout).result(timeout=60)
        except Exception as e:
            self.log(f"세션 삭제 중 오류: {e}", "error")
        self.state.update(logged_in=False, step="대기", progress=0)

    # ---------- earning_kakao 결과 가져오기 ----------

    def import_results(self, path: Path) -> dict:
        if not path.exists():
            raise SettingsError(f"파일이 없습니다: {path}")
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise SettingsError(f"JSON 형식이 아닙니다: {e}") from e
        if not isinstance(items, list):
            raise SettingsError("results.json 형식이 아닙니다 (목록이어야 함).")
        counts = {"insert": 0, "update": 0, "skip": 0, "renamed": 0}
        for item in items:
            if not isinstance(item, dict):
                counts["skip"] += 1
                continue
            item = dict(item)
            if is_suspect_name(item.get("name")):
                # 예전 파서 오류 → 상품 페이지 제목으로 복구 (사이트 부담을 줄이려고 간격을 둠)
                title = fetch_product_title(item.get("product_url") or "")
                time.sleep(0.5)
                if title:
                    item["name"] = title
                    counts["renamed"] += 1
                elif not item.get("name") or item["name"] in NOISE_LINES:
                    item["name"] = f"(상품명 확인 필요) {item.get('product_id') or ''}".strip()
            row = self.to_row(item)
            if not row or not row["link"]:
                counts["skip"] += 1
                continue
            counts[self.db.upsert_kakao_product(row)] += 1
        self.log(f"가져오기 완료: 새 상품 {counts['insert']}개, 갱신 {counts['update']}개, "
                 f"상품명 복구 {counts['renamed']}개, 건너뜀 {counts['skip']}개", "success")
        return counts
