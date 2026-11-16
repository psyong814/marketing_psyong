"""Flask 서버: frontend/ 정적 파일 + /api/* JSON API.

실행: .\\.venv\\Scripts\\python.exe app.py  →  http://127.0.0.1:5000
"""

from urllib.parse import urlparse

from flask import Flask, abort, jsonify, request, send_from_directory

from pathlib import Path

from backend import app_settings, env_store
from backend.kakao import settings_store as kakao_settings
from backend.kakao.service import KakaoService
from backend.llm import LLMError, engine_status, gemini_model
from backend.llm.gemini_client import GeminiClient
from backend.services.collect_service import collector_status, run_collect
from backend.services.filter_service import UserError, run_filter
from backend.services.link_service import build_links, update_link
from backend.services.settings_service import test_connection
from backend.settings import ROOT, env, load_settings
from backend.storage.db import DB

FRONTEND = ROOT / "frontend"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}

settings = load_settings()
app_settings.apply(settings)  # 화면에서 바꾼 개수·기준값 (data/app_settings.json)
db = DB(ROOT / settings["storage"]["db_path"])
kakao = KakaoService(db)

app = Flask(__name__, static_folder=str(FRONTEND), static_url_path="/static")
app.json.ensure_ascii = False


@app.before_request
def local_only_guard():
    """API 키를 다루므로, 이 PC의 브라우저에서 온 요청만 받는다.

    - Host 검사: 외부에서 접속하거나 DNS 리바인딩으로 들어오는 요청 차단
    - Origin 검사: 다른 웹사이트가 몰래 이 서버로 POST 하는 것(CSRF) 차단
    """
    if not request.path.startswith("/api/"):
        return None
    host = request.host.rsplit(":", 1)[0] if not request.host.startswith("[") else request.host.split("]")[0] + "]"
    if host not in LOCAL_HOSTS:
        abort(403, "로컬(127.0.0.1)에서만 사용할 수 있습니다.")
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            abort(403, "다른 사이트에서 온 요청은 허용하지 않습니다.")
    return None


@app.get("/")
def index():
    return send_from_directory(FRONTEND, "index.html")


# ---------- 1단계: 트렌드 수집 ----------

@app.get("/api/status")
def api_status():
    return jsonify({"collectors": collector_status(settings, db), "llm": engine_status(settings)})


@app.post("/api/collect")
def api_collect():
    return jsonify(run_collect(settings, db))


@app.get("/api/trends/latest")
def api_trends_latest():
    return jsonify(db.latest_run() or {"run_id": None, "sources": [], "trends": []})


# ---------- 2단계: 필터 · 상품 매칭 ----------

@app.post("/api/filter")
def api_filter():
    return jsonify(run_filter(settings, db))


@app.get("/api/filter/latest")
def api_filter_latest():
    return jsonify(db.latest_analysis() or {"id": None, "keywords": [], "summary": {}})


# ---------- 3단계: 제휴 링크 확정 ----------

@app.post("/api/links/build")
def api_links_build():
    return jsonify(build_links(settings, db))


@app.get("/api/links/latest")
def api_links_latest():
    return jsonify(db.latest_links())


@app.post("/api/links/<int:link_id>")
def api_link_update(link_id):
    body = request.get_json()
    if not isinstance(body, dict):
        raise UserError("요청 형식이 올바르지 않습니다.")
    return jsonify(update_link(db, link_id, body))


# ---------- 단계별 초기화 ----------

@app.post("/api/reset/<int:step>")
def api_reset(step):
    """step 단계와 그 뒤 단계의 결과를 지움 (카카오 상품·설정은 그대로)"""
    if step not in (1, 2, 3):
        raise UserError("초기화할 수 없는 단계입니다.")
    return jsonify({"deleted": db.reset_from(step)})


@app.post("/api/kakao/products/clear")
def api_kakao_products_clear():
    if kakao.busy():
        raise UserError("카카오 수집이 진행 중이라 지울 수 없습니다.")
    return jsonify({"deleted": db.clear_kakao_products()})


# ---------- 시스템 설정 (API 키) ----------

@app.get("/api/settings")
def api_settings():
    return jsonify(env_store.public_view())


@app.post("/api/settings")
def api_settings_save():
    body = request.get_json()  # Content-Type: application/json 이 아니면 415
    if not isinstance(body, dict):
        raise env_store.EnvStoreError("요청 형식이 올바르지 않습니다.")
    changed = env_store.update(body.get("values") or {}, body.get("clear") or [])
    return jsonify({"changed": changed, **env_store.public_view()})


@app.get("/api/app-settings")
def api_app_settings():
    return jsonify(app_settings.public_view(settings))


@app.post("/api/app-settings")
def api_app_settings_save():
    body = request.get_json()
    if not isinstance(body, dict):
        raise UserError("요청 형식이 올바르지 않습니다.")
    return jsonify(app_settings.save(settings, body.get("values") or {}))


@app.post("/api/settings/test/<service>")
def api_settings_test(service):
    return jsonify(test_connection(service, settings))


# ---------- 카카오 선물하기 큐레이터 ----------

def _json_body() -> dict:
    body = request.get_json()  # Content-Type: application/json 이 아니면 415
    if not isinstance(body, dict):
        raise UserError("요청 형식이 올바르지 않습니다.")
    return body


@app.get("/api/kakao/settings")
def api_kakao_settings():
    return jsonify(kakao_settings.public_view())


@app.post("/api/kakao/settings")
def api_kakao_settings_save():
    body = _json_body()
    kakao_settings.save(body)
    kakao.log("카카오 설정을 저장했습니다." + (" (비밀번호 변경됨)" if body.get("password") else ""))
    return jsonify(kakao_settings.public_view())


@app.post("/api/kakao/settings/delete")
def api_kakao_settings_delete():
    kakao_settings.delete_credentials()
    kakao.log("저장된 카카오 아이디/비밀번호를 삭제했습니다.")
    return jsonify(kakao_settings.public_view())


@app.get("/api/kakao/status")
def api_kakao_status():
    return jsonify(kakao.status())


@app.get("/api/kakao/logs")
def api_kakao_logs():
    return jsonify(kakao.logs_since(request.args.get("since", 0, type=int)))


@app.post("/api/kakao/run")
def api_kakao_run():
    kakao.run()
    return jsonify({"ok": True})


@app.post("/api/kakao/collect")
def api_kakao_collect():
    body = request.get_json(silent=True) or {}
    kakao.collect(body.get("max_items"), body.get("interval"))
    return jsonify({"ok": True})


@app.post("/api/kakao/stop")
def api_kakao_stop():
    kakao.stop()
    return jsonify({"ok": True})


@app.post("/api/kakao/browser/close")
def api_kakao_browser_close():
    kakao.close_browser()
    return jsonify({"ok": True})


@app.post("/api/kakao/session/clear")
def api_kakao_session_clear():
    kakao.clear_session()
    return jsonify({"ok": True})


@app.get("/api/kakao/products")
def api_kakao_products():
    return jsonify({
        "products": db.list_kakao_products(),
        "import_path": settings.get("kakao", {}).get("import_path", ""),
    })


@app.post("/api/kakao/products/<int:pid>")
def api_kakao_product_update(pid):
    body = _json_body()
    tags = body.get("tags")
    if tags is not None:
        tags = ", ".join(t.strip() for t in str(tags).replace("\n", ",").split(",") if t.strip())[:300]
    active = body.get("active")
    if not db.update_kakao_product(pid, tags, None if active is None else bool(active)):
        raise UserError("상품을 찾을 수 없거나 바꿀 내용이 없습니다.")
    return jsonify({"ok": True})


@app.post("/api/kakao/products/<int:pid>/delete")
def api_kakao_product_delete(pid):
    if not db.delete_kakao_product(pid):
        raise UserError("상품을 찾을 수 없습니다.")
    return jsonify({"ok": True})


@app.post("/api/kakao/import")
def api_kakao_import():
    """earning_kakao 의 data/results.json 가져오기 (경로는 settings.yaml 의 kakao.import_path)"""
    path = settings.get("kakao", {}).get("import_path", "")
    if not path:
        raise UserError("config/settings.yaml 의 kakao.import_path 가 비어 있습니다.")
    return jsonify(kakao.import_results(Path(path)))


# ---------- LLM ----------

@app.get("/api/llm/models")
def api_llm_models():
    """Gemini 키로 사용할 수 있는 모델 목록."""
    key = env("GEMINI_API_KEY")
    if not key:
        raise UserError("Gemini API 키가 없습니다. 설정 화면에서 입력하세요.")
    conf = settings.get("llm", {}).get("gemini", {})
    client = GeminiClient(key, gemini_model(settings), float(conf.get("timeout", 60)))
    return jsonify({"current": client.model, "models": client.list_models()})


@app.errorhandler(LLMError)
def handle_llm_error(e):
    return jsonify({"error": str(e)}), 502


@app.errorhandler(Exception)
def handle_error(e):
    code = getattr(e, "code", 500)
    if not isinstance(code, int):
        code = 500
    message = getattr(e, "description", None) or str(e)
    return jsonify({"error": message}), code


if __name__ == "__main__":
    server = settings.get("server", {})
    app.run(
        host=server.get("host", "127.0.0.1"),
        port=int(server.get("port", 5000)),
        debug=bool(server.get("debug", False)),
        # 자동 재시작을 켜면 코드가 바뀔 때 카카오 자동화 브라우저가 끊기므로 기본은 끔
        use_reloader=bool(server.get("reloader", False)),
        threaded=True,
    )
