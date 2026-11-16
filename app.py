"""Flask 서버: frontend/ 정적 파일 + /api/* JSON API.

실행: .\\.venv\\Scripts\\python.exe app.py  →  http://127.0.0.1:5000
"""

from flask import Flask, jsonify, send_from_directory

from backend.services.collect_service import collector_status, run_collect
from backend.settings import ROOT, load_settings
from backend.storage.db import DB

FRONTEND = ROOT / "frontend"

settings = load_settings()
db = DB(ROOT / settings["storage"]["db_path"])

app = Flask(__name__, static_folder=str(FRONTEND), static_url_path="/static")
app.json.ensure_ascii = False


@app.get("/")
def index():
    return send_from_directory(FRONTEND, "index.html")


@app.get("/api/status")
def api_status():
    return jsonify({"collectors": collector_status(settings)})


@app.post("/api/collect")
def api_collect():
    return jsonify(run_collect(settings, db))


@app.get("/api/trends/latest")
def api_trends_latest():
    return jsonify(db.latest_run() or {"run_id": None, "sources": [], "trends": []})


@app.errorhandler(Exception)
def handle_error(e):
    code = getattr(e, "code", 500)
    if not isinstance(code, int):
        code = 500
    return jsonify({"error": str(e)}), code


if __name__ == "__main__":
    server = settings.get("server", {})
    app.run(
        host=server.get("host", "127.0.0.1"),
        port=int(server.get("port", 5000)),
        debug=bool(server.get("debug", False)),
    )
