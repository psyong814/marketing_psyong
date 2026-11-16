"""카카오 선물하기 설정 저장소 (earning_kakao/settings_store.py 기반)

- 아이디·실행 옵션 : data/kakao_settings.json
- 비밀번호        : 파일에 저장하지 않고 OS 보안 저장소에 저장
    Windows → 자격 증명 관리자 (제어판 > 사용자 계정 > 자격 증명 관리자 > Windows 자격 증명 > "marketing_kakao")
"""

import json

import keyring

from backend.settings import ROOT

from .automation import DEFAULT_MOBILE_DEVICE, MOBILE_DEVICES

SETTINGS_FILE = ROOT / "data" / "kakao_settings.json"
KEYRING_SERVICE = "marketing_kakao"

DEFAULTS = {
    "user_id": "",
    "headless": False,                  # 브라우저 창 숨김 (카카오가 차단할 수 있어 권장하지 않음)
    "login_timeout": 180,               # 카카오톡 승인/보안문자를 직접 처리할 시간(초)
    "open_curator": True,               # 로그인 후 [큐레이터] 메뉴 클릭
    "mobile_mode": True,                # 모바일 화면으로 전환 + 새로고침 (큐레이터는 모바일 전용)
    "mobile_device": DEFAULT_MOBILE_DEVICE,
    "mobile_method": "devtools",        # devtools: F12→Ctrl+Shift+M→F5 키 입력 / emulation: 코드로 전환
    "close_devtools": True,             # 모바일 전환 후 개발자 도구 창 닫기
    "auto_collect": True,               # 실행 때 [링크 생성]을 눌러 링크 수집
    "season_first": True,               # 오늘 시즌 키워드와 맞는 상품부터 수집하고, 남는 개수는 나머지로 채움
    "season_method": "search",          # search: 큐레이터 검색창에 시즌 키워드 입력 / scan: 목록을 훑으며 상품명 비교
    "search_per_keyword": 5,            # 검색 방식일 때 키워드 하나당 최대 수집 개수
    "collect_max": 20,                  # 최대 수집 개수
    "collect_interval": 1.5,            # 상품 사이 대기(초) - 너무 빠르면 사이트가 막을 수 있음
}


class SettingsError(ValueError):
    code = 400


def load() -> dict:
    data = dict(DEFAULTS)
    if SETTINGS_FILE.exists():
        try:
            data.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return {k: data.get(k, v) for k, v in DEFAULTS.items()}


def _write(data: dict) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    clean = {k: data.get(k, v) for k, v in DEFAULTS.items()}
    SETTINGS_FILE.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")


def get_password(user_id: str) -> str | None:
    if not user_id:
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE, user_id)
    except Exception as e:
        raise SettingsError(f"비밀번호 저장소를 읽을 수 없습니다: {e}") from e


def _set_password(user_id: str, password: str) -> None:
    try:
        keyring.set_password(KEYRING_SERVICE, user_id, password)
    except Exception as e:
        raise SettingsError(f"비밀번호를 저장할 수 없습니다: {e}") from e


def _delete_password(user_id: str) -> None:
    if not user_id:
        return
    try:
        keyring.delete_password(KEYRING_SERVICE, user_id)
    except Exception:
        pass  # 원래 없던 경우


def public_view() -> dict:
    """화면에 보낼 설정 (비밀번호 값은 절대 보내지 않음)"""
    data = load()
    try:
        has_pw = bool(get_password(data["user_id"]))
    except SettingsError:
        has_pw = False
    return {**data, "has_password": has_pw, "mobile_devices": MOBILE_DEVICES}


def _int(v, default, lo, hi):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def _float(v, default, lo, hi):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def _bool(body: dict, key: str, old: dict) -> bool:
    return bool(body[key]) if key in body and body[key] is not None else old[key]


def save(body: dict) -> None:
    old = load()
    old_id = old["user_id"]
    user_id = str(body.get("user_id", old_id) or "").strip()
    password = body.get("password") or ""
    if any(c in user_id + password for c in "\r\n\0"):
        raise SettingsError("줄바꿈 문자는 넣을 수 없습니다.")

    if password:
        if not user_id:
            raise SettingsError("아이디를 입력하세요.")
        _set_password(user_id, password)
        if old_id and old_id != user_id:
            _delete_password(old_id)
    elif old_id and old_id != user_id:
        # 아이디만 바뀐 경우: 기존 비밀번호를 새 아이디로 옮김
        old_pw = get_password(old_id)
        if old_pw and user_id:
            _set_password(user_id, old_pw)
        _delete_password(old_id)

    method = body.get("mobile_method")
    device = body.get("mobile_device")
    _write({
        "user_id": user_id,
        "headless": _bool(body, "headless", old),
        "login_timeout": _int(body.get("login_timeout"), old["login_timeout"], 30, 600),
        "open_curator": _bool(body, "open_curator", old),
        "mobile_mode": _bool(body, "mobile_mode", old),
        "mobile_device": device if device in MOBILE_DEVICES else old["mobile_device"],
        "mobile_method": method if method in ("devtools", "emulation") else old["mobile_method"],
        "close_devtools": _bool(body, "close_devtools", old),
        "auto_collect": _bool(body, "auto_collect", old),
        "season_first": _bool(body, "season_first", old),
        "season_method": body.get("season_method") if body.get("season_method") in ("search", "scan")
                         else old["season_method"],
        "search_per_keyword": _int(body.get("search_per_keyword"), old["search_per_keyword"], 1, 50),
        "collect_max": _int(body.get("collect_max"), old["collect_max"], 1, 500),
        "collect_interval": _float(body.get("collect_interval"), old["collect_interval"], 0.5, 30),
    })


def delete_credentials() -> None:
    data = load()
    _delete_password(data["user_id"])
    data["user_id"] = ""
    _write(data)


def credentials() -> tuple[str, str | None]:
    data = load()
    return data["user_id"], get_password(data["user_id"])
