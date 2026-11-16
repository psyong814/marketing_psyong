import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

SCHEMA = """
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
"""


class DB:
    """요청마다 커넥션을 새로 여는 단순한 SQLite 래퍼 (Flask 멀티스레드 대응)."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def save_run(self, run_id: str, sources: list[dict], trends) -> None:
        now = datetime.now().isoformat(timespec="seconds")
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
