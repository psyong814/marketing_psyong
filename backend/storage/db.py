import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA = """
-- 1단계: 수집 회차 / 수집 결과
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    started_at  TEXT NOT NULL,
    sources     TEXT NOT NULL          -- 소스별 수집 결과 요약(JSON)
);
CREATE TABLE IF NOT EXISTS trends (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    source       TEXT NOT NULL,
    keyword      TEXT NOT NULL,
    rank         INTEGER,
    score        REAL,
    extra        TEXT,
    collected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trends_run ON trends(run_id);
CREATE INDEX IF NOT EXISTS idx_trends_keyword ON trends(keyword);

-- 2단계: 필터·상품 매칭 회차 / 키워드 판별 / 매칭 상품
CREATE TABLE IF NOT EXISTS analyses (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,        -- 어떤 수집 회차를 분석했는지
    analyzed_at  TEXT NOT NULL,
    engine       TEXT NOT NULL,        -- rule / gemini:모델명
    summary      TEXT NOT NULL         -- 통과·제외 수, 경고 메시지 등(JSON)
);
CREATE TABLE IF NOT EXISTS keywords (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id     INTEGER NOT NULL,
    source          TEXT NOT NULL,
    keyword         TEXT NOT NULL,
    commercial      INTEGER NOT NULL,  -- 1: 상품 키워드, 0: 제외
    score           INTEGER NOT NULL,
    category        TEXT,
    search_queries  TEXT,              -- JSON 배열
    reason          TEXT,
    engine          TEXT
);
CREATE INDEX IF NOT EXISTS idx_keywords_analysis ON keywords(analysis_id);
CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword_id  INTEGER NOT NULL,
    platform    TEXT NOT NULL,
    product_id  TEXT,
    name        TEXT NOT NULL,
    price       INTEGER,
    image       TEXT,
    url         TEXT,
    is_rocket   INTEGER,
    category    TEXT,
    query       TEXT,                  -- 어떤 검색어로 찾았는지
    mock        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_products_keyword ON products(keyword_id);

-- 카카오 선물하기 큐레이터 상품·링크 (브라우저 자동화로 수집, 또는 earning_kakao results.json 가져오기)
CREATE TABLE IF NOT EXISTS kakao_products (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    product_key   TEXT NOT NULL UNIQUE,  -- 상품 번호(없으면 상품명) : 중복 수집 방지
    product_id    TEXT,
    brand         TEXT,
    name          TEXT NOT NULL,
    discount      TEXT,                  -- 예: "20%"
    price         INTEGER,               -- 판매가(원)
    benefit       INTEGER,               -- 최대혜택가(원)
    badge         TEXT,                  -- 예: "최근 구매 41명"
    image         TEXT,
    product_url   TEXT,
    link          TEXT,                  -- 큐레이터 공유 링크 (gift-link.kakao.com/...)
    link_source   TEXT,
    disclosure    TEXT,                  -- 링크 복사 때 함께 복사된 카카오 공식 안내 문구
    tags          TEXT NOT NULL DEFAULT '',   -- 매칭용 태그 (쉼표 구분, 화면에서 수정)
    active        INTEGER NOT NULL DEFAULT 1, -- 0 이면 매칭·홍보에서 제외
    collected_at  TEXT,
    updated_at    TEXT NOT NULL
);

-- 3단계: 홍보할 상품·제휴 링크 확정 (2단계 분석 1회당 키워드별 1행)
CREATE TABLE IF NOT EXISTS links (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id   INTEGER NOT NULL,      -- 어느 2단계 분석에서 만들었는지
    keyword       TEXT NOT NULL,
    source        TEXT,
    score         INTEGER,
    reason        TEXT,
    options       TEXT NOT NULL,         -- 고를 수 있는 카카오 상품 후보 (JSON 배열)
    product_id    TEXT,                  -- 선택한 상품
    product_name  TEXT,
    price         INTEGER,
    image         TEXT,
    product_url   TEXT,
    link          TEXT,                  -- 큐레이터 공유 링크
    disclosure    TEXT,                  -- 카카오 공식 안내 문구 (4단계 문구에 사용)
    link_ok       INTEGER NOT NULL DEFAULT 0,  -- 링크 형식 확인 결과
    selected      INTEGER NOT NULL DEFAULT 0,  -- 1: 홍보 대상으로 확정
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_links_analysis ON links(analysis_id);
-- 3단계 후보를 만들 때 상품이 겹쳐 빠진 키워드 (화면에 이유를 보여주려고 저장)
CREATE TABLE IF NOT EXISTS link_builds (
    analysis_id   INTEGER PRIMARY KEY,
    built_at      TEXT NOT NULL,
    skipped       TEXT NOT NULL          -- JSON: [{keyword, score, product, taken_by}]
);

-- LLM 판별 결과 캐시 (무료 한도 절약)
CREATE TABLE IF NOT EXISTS llm_cache (
    keyword     TEXT NOT NULL,
    engine      TEXT NOT NULL,
    result      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (keyword, engine)
);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class DB:
    """요청마다 커넥션을 새로 여는 단순한 SQLite 래퍼 (Flask 멀티스레드 대응)."""

    # 다른 프로그램(PyCharm DB Navigator 등)이 잠깐 DB 를 잡고 있으면 최대 이 시간(초)까지 기다린다.
    BUSY_TIMEOUT = 15

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._enable_wal()
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def _enable_wal(self) -> None:
        """WAL 모드: 다른 프로그램이 읽는 중이어도 쓰기가 막히지 않는다. (설정은 DB 파일에 영구 저장)

        전환 순간에는 독점 잠금이 필요하므로, 다른 프로그램이 DB 를 잡고 있으면 실패할 수 있다.
        실패해도 서버는 계속 뜨고, 다음 실행 때 다시 시도한다.
        """
        try:
            conn = sqlite3.connect(self.path, timeout=3)
            try:
                mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            finally:
                conn.close()
            if mode.lower() != "wal":
                print(f"[DB] WAL 전환 실패 (현재 {mode}). 다른 프로그램의 DB 연결을 끊고 서버를 재시작하세요.")
        except sqlite3.OperationalError as e:
            print(f"[DB] WAL 전환 실패: {e}. 다른 프로그램(DB Navigator 등)의 연결을 끊고 서버를 재시작하세요.")

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=self.BUSY_TIMEOUT)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---------- 1단계 ----------

    def save_run(self, run_id: str, sources: list[dict], trends) -> None:
        now = _now()
        rows = [
            (run_id, t.source, t.keyword, t.rank, t.score, json.dumps(t.extra, ensure_ascii=False), now)
            for t in trends
        ]
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO runs (run_id, started_at, sources) VALUES (?,?,?)",
                (run_id, now, json.dumps(sources, ensure_ascii=False)),
            )
            conn.executemany(
                "INSERT INTO trends (run_id, source, keyword, rank, score, extra, collected_at) VALUES (?,?,?,?,?,?,?)",
                rows,
            )

    def latest_run(self) -> dict | None:
        with self._conn() as conn:
            run = conn.execute("SELECT * FROM runs ORDER BY started_at DESC, run_id DESC LIMIT 1").fetchone()
            if not run:
                return None
            trends = conn.execute(
                "SELECT source, keyword, rank, score, extra FROM trends WHERE run_id = ? ORDER BY source, rank",
                (run["run_id"],),
            ).fetchall()
        return {
            "run_id": run["run_id"],
            "started_at": run["started_at"],
            "sources": json.loads(run["sources"]),
            "trends": [{**dict(t), "extra": json.loads(t["extra"] or "{}")} for t in trends],
        }

    # ---------- 2단계 ----------

    def save_analysis(self, run_id: str, engine: str, summary: dict, keywords: list[dict]) -> int:
        """keywords: [{source, keyword, commercial, score, category, search_queries, reason, engine, products:[...]}]"""
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO analyses (run_id, analyzed_at, engine, summary) VALUES (?,?,?,?)",
                (run_id, _now(), engine, json.dumps(summary, ensure_ascii=False)),
            )
            analysis_id = cur.lastrowid
            for k in keywords:
                kcur = conn.execute(
                    """INSERT INTO keywords (analysis_id, source, keyword, commercial, score, category,
                                             search_queries, reason, engine)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        analysis_id, k["source"], k["keyword"], int(k["commercial"]), k["score"],
                        k.get("category", ""), json.dumps(k.get("search_queries", []), ensure_ascii=False),
                        k.get("reason", ""), k.get("engine", ""),
                    ),
                )
                for p in k.get("products", []):
                    conn.execute(
                        """INSERT INTO products (keyword_id, platform, product_id, name, price, image, url,
                                                 is_rocket, category, query, mock)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            kcur.lastrowid, p["platform"], str(p.get("product_id") or ""), p["name"], p.get("price"),
                            p.get("image", ""), p.get("url", ""), int(bool(p.get("is_rocket"))),
                            p.get("category", ""), p.get("query", ""), int(bool(p.get("mock"))),
                        ),
                    )
        return analysis_id

    def latest_analysis(self) -> dict | None:
        with self._conn() as conn:
            a = conn.execute("SELECT * FROM analyses ORDER BY id DESC LIMIT 1").fetchone()
            if not a:
                return None
            kws = conn.execute(
                "SELECT * FROM keywords WHERE analysis_id = ? ORDER BY commercial DESC, score DESC, id",
                (a["id"],),
            ).fetchall()
            prods = conn.execute(
                "SELECT p.* FROM products p JOIN keywords k ON k.id = p.keyword_id WHERE k.analysis_id = ? ORDER BY p.id",
                (a["id"],),
            ).fetchall()

        by_kw: dict[int, list] = {}
        for p in prods:
            d = dict(p)
            d["is_rocket"], d["mock"] = bool(d["is_rocket"]), bool(d["mock"])
            by_kw.setdefault(p["keyword_id"], []).append(d)

        return {
            "id": a["id"],
            "run_id": a["run_id"],
            "analyzed_at": a["analyzed_at"],
            "engine": a["engine"],
            "summary": json.loads(a["summary"]),
            "keywords": [
                {
                    **dict(k),
                    "commercial": bool(k["commercial"]),
                    "search_queries": json.loads(k["search_queries"] or "[]"),
                    "products": by_kw.get(k["id"], []),
                }
                for k in kws
            ],
        }

    # ---------- 카카오 선물하기 상품 ----------

    KAKAO_COLS = ("product_key", "product_id", "brand", "name", "discount", "price", "benefit", "badge",
                  "image", "product_url", "link", "link_source", "disclosure", "collected_at")

    def upsert_kakao_product(self, row: dict) -> str:
        """같은 상품이면 정보·링크를 갱신 (태그·활성 여부는 유지). 'insert' / 'update' 반환"""
        now = _now()
        values = [row.get(c) for c in self.KAKAO_COLS]
        with self._conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM kakao_products WHERE product_key = ?", (row["product_key"],)
            ).fetchone()
            if exists:
                sets = ", ".join(f"{c} = COALESCE(NULLIF(?, ''), {c})" for c in self.KAKAO_COLS[1:])
                conn.execute(
                    f"UPDATE kakao_products SET {sets}, updated_at = ? WHERE product_key = ?",
                    (*values[1:], now, row["product_key"]),
                )
                return "update"
            cols = ", ".join(self.KAKAO_COLS)
            marks = ", ".join("?" for _ in self.KAKAO_COLS)
            conn.execute(
                f"INSERT INTO kakao_products ({cols}, tags, updated_at) VALUES ({marks}, ?, ?)",
                (*values, row.get("tags", ""), now),
            )
            return "insert"

    def list_kakao_products(self, active_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM kakao_products"
        if active_only:
            sql += " WHERE active = 1 AND link IS NOT NULL AND link != ''"
        with self._conn() as conn:
            rows = conn.execute(sql + " ORDER BY id DESC").fetchall()
        return [{**dict(r), "active": bool(r["active"])} for r in rows]

    def kakao_product_keys(self) -> set[str]:
        with self._conn() as conn:
            return {r[0] for r in conn.execute("SELECT product_key FROM kakao_products WHERE link != ''")}

    def update_kakao_product(self, pid: int, tags: str | None = None, active: bool | None = None) -> bool:
        sets, args = [], []
        if tags is not None:
            sets.append("tags = ?")
            args.append(tags)
        if active is not None:
            sets.append("active = ?")
            args.append(int(active))
        if not sets:
            return False
        with self._conn() as conn:
            cur = conn.execute(
                f"UPDATE kakao_products SET {', '.join(sets)}, updated_at = ? WHERE id = ?", (*args, _now(), pid)
            )
            return cur.rowcount > 0

    def add_kakao_tag(self, product_key: str, tag: str) -> None:
        """상품 태그에 추가 (이미 있으면 그대로)"""
        tag = tag.strip()
        if not tag:
            return
        with self._conn() as conn:
            row = conn.execute("SELECT tags FROM kakao_products WHERE product_key = ?", (product_key,)).fetchone()
            if not row:
                return
            tags = [t.strip() for t in (row["tags"] or "").split(",") if t.strip()]
            if tag not in tags:
                conn.execute("UPDATE kakao_products SET tags = ?, updated_at = ? WHERE product_key = ?",
                             (", ".join([*tags, tag]), _now(), product_key))

    def delete_kakao_product(self, pid: int) -> bool:
        with self._conn() as conn:
            return conn.execute("DELETE FROM kakao_products WHERE id = ?", (pid,)).rowcount > 0

    # ---------- 3단계: 링크 확정 ----------

    LINK_COLS = ("keyword", "source", "score", "reason", "options", "product_id", "product_name", "price",
                 "image", "product_url", "link", "disclosure", "link_ok", "selected")

    def replace_links(self, analysis_id: int, rows: list[dict], skipped: list[dict] | None = None) -> None:
        """같은 분석으로 만든 기존 후보를 지우고 새로 저장"""
        now = _now()
        with self._conn() as conn:
            conn.execute("DELETE FROM links WHERE analysis_id = ?", (analysis_id,))
            conn.execute(
                "INSERT OR REPLACE INTO link_builds (analysis_id, built_at, skipped) VALUES (?,?,?)",
                (analysis_id, now, json.dumps(skipped or [], ensure_ascii=False)),
            )
            cols = ", ".join(self.LINK_COLS)
            marks = ", ".join("?" for _ in self.LINK_COLS)
            conn.executemany(
                f"INSERT INTO links (analysis_id, {cols}, created_at, updated_at) VALUES (?, {marks}, ?, ?)",
                [(analysis_id, *[r.get(c) for c in self.LINK_COLS], now, now) for r in rows],
            )

    @staticmethod
    def _link_row(r) -> dict:
        d = dict(r)
        d["options"] = json.loads(d["options"] or "[]")
        d["selected"], d["link_ok"] = bool(d["selected"]), bool(d["link_ok"])
        return d

    def latest_links(self) -> dict:
        with self._conn() as conn:
            row = conn.execute("SELECT MAX(analysis_id) AS aid, MAX(created_at) AS built FROM links "
                               "WHERE analysis_id = (SELECT MAX(analysis_id) FROM links)").fetchone()
            latest_analysis = conn.execute("SELECT MAX(id) FROM analyses").fetchone()[0]
            if not row or row["aid"] is None:
                return {"analysis_id": None, "latest_analysis_id": latest_analysis, "links": []}
            rows = conn.execute("SELECT * FROM links WHERE analysis_id = ? ORDER BY selected DESC, score DESC, id",
                                (row["aid"],)).fetchall()
            build = conn.execute("SELECT skipped FROM link_builds WHERE analysis_id = ?", (row["aid"],)).fetchone()
        return {
            "analysis_id": row["aid"],
            "built_at": row["built"],
            "latest_analysis_id": latest_analysis,
            "links": [self._link_row(r) for r in rows],
            "skipped": json.loads(build["skipped"]) if build else [],
        }

    def get_link(self, link_id: int) -> dict | None:
        with self._conn() as conn:
            r = conn.execute("SELECT * FROM links WHERE id = ?", (link_id,)).fetchone()
        return self._link_row(r) if r else None

    def update_link(self, link_id: int, fields: dict) -> None:
        allowed = {k: v for k, v in fields.items() if k in self.LINK_COLS}
        if not allowed:
            return
        sets = ", ".join(f"{k} = ?" for k in allowed)
        with self._conn() as conn:
            conn.execute(f"UPDATE links SET {sets}, updated_at = ? WHERE id = ?", (*allowed.values(), _now(), link_id))

    def selected_links(self) -> list[dict]:
        """4단계에서 쓸 확정 링크 (가장 최근 후보 목록 기준)"""
        data = self.latest_links()
        return [l for l in data["links"] if l["selected"]]

    # ---------- 초기화 ----------

    # 단계별로 지울 테이블. 앞 단계를 지우면 그 결과로 만든 뒤 단계도 함께 지운다.
    STEP_TABLES = {
        3: ["links", "link_builds"],
        2: ["products", "keywords", "analyses"],
        1: ["trends", "runs"],
    }

    def reset_from(self, step: int) -> dict:
        """step 단계와 그 뒤 단계 데이터를 모두 삭제. {테이블: 지운 행 수}"""
        if step not in self.STEP_TABLES:
            raise ValueError(f"알 수 없는 단계: {step}")
        deleted = {}
        with self._conn() as conn:
            for s in sorted(self.STEP_TABLES, reverse=True):
                if s < step:
                    break
                for table in self.STEP_TABLES[s]:
                    deleted[table] = conn.execute(f"DELETE FROM {table}").rowcount
        return deleted

    def clear_kakao_products(self) -> int:
        with self._conn() as conn:
            return conn.execute("DELETE FROM kakao_products").rowcount

    # ---------- LLM 캐시 ----------

    def get_cached(self, keyword: str, engine: str, max_age_days: int) -> dict | None:
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat(timespec="seconds")
        with self._conn() as conn:
            row = conn.execute(
                "SELECT result FROM llm_cache WHERE keyword = ? AND engine = ? AND created_at >= ?",
                (keyword, engine, cutoff),
            ).fetchone()
        return json.loads(row["result"]) if row else None

    def set_cached(self, items: list[tuple[str, str, dict]]) -> None:
        """items: [(keyword, engine, result_dict)]"""
        now = _now()
        with self._conn() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO llm_cache (keyword, engine, result, created_at) VALUES (?,?,?,?)",
                [(k, e, json.dumps(r, ensure_ascii=False), now) for k, e, r in items],
            )
