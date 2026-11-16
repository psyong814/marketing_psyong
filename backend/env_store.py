"""API 키 등 민감한 설정을 .env 파일에 읽고 쓰는 저장소.

- 화면(설정 페이지)에서 입력한 값을 .env 에 저장하고, 실행 중인 서버에도 즉시 반영한다(재시작 불필요).
- 비밀 값은 화면으로 다시 내보내지 않고 마스킹된 형태(••••abcd)만 돌려준다.
"""

import os
import tempfile
import threading

from backend.settings import ROOT

ENV_PATH = ROOT / ".env"
MAX_LEN = 500
_lock = threading.Lock()

# 설정 화면에 노출할 항목 (여기에 추가하면 화면에도 자동으로 나타난다)
FIELDS = [
    {
        "name": "GEMINI_API_KEY",
        "group": "gemini",
        "label": "API 키",
        "secret": True,
        "help": "aistudio.google.com → Get API key → Create API key",
    },
    {
        "name": "GEMINI_MODEL",
        "group": "gemini",
        "label": "모델",
        "secret": False,
        "help": "비워두면 config/settings.yaml 의 llm.gemini.model 값을 사용",
    },
    # 쿠팡 파트너스 API 승인 후 COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY 항목과 'coupang' 그룹을 추가
]
FIELD_NAMES = {f["name"] for f in FIELDS}

# 화면에 표시되는 순서대로 (dict 는 JSON 변환 시 키 정렬되므로 list 사용)
GROUPS = [
    {"id": "gemini", "label": "Google AI Studio (Gemini)", "desc": "2단계 키워드 판별 · 4단계 문구 생성. 없으면 규칙 기반으로 동작"},
]


class EnvStoreError(ValueError):
    code = 400


def _mask(value: str) -> str:
    if not value:
        return ""
    return "••••••••" + value[-4:] if len(value) > 8 else "••••"


def _read_lines() -> list[str]:
    if not ENV_PATH.exists():
        return []
    return ENV_PATH.read_text(encoding="utf-8").splitlines()


def _parse(line: str) -> tuple[str, str] | None:
    s = line.strip()
    if not s or s.startswith("#") or "=" not in s:
        return None
    key, value = s.split("=", 1)
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return key.strip().removeprefix("export ").strip(), value


def _validate(name: str, value: str) -> str:
    if name not in FIELD_NAMES:
        raise EnvStoreError(f"설정할 수 없는 항목입니다: {name}")
    value = value.strip()
    if any(c in value for c in "\r\n\0"):
        raise EnvStoreError(f"{name}: 줄바꿈 문자는 넣을 수 없습니다.")
    if len(value) > MAX_LEN:
        raise EnvStoreError(f"{name}: 값이 너무 깁니다.")
    return value


def update(values: dict[str, str], clear: list[str]) -> list[str]:
    """values 의 항목을 저장(빈 문자열은 '변경 없음'), clear 의 항목은 삭제. 변경된 이름 목록을 돌려준다."""
    sets = {}
    for name, value in (values or {}).items():
        v = _validate(name, str(value))
        if v:
            sets[name] = v
    removes = set()
    for name in clear or []:
        _validate(name, "")
        removes.add(name)
    if not sets and not removes:
        return []

    with _lock:
        out, done = [], set()
        for line in _read_lines():
            parsed = _parse(line)
            key = parsed[0] if parsed else None
            if key in removes:
                continue
            if key in sets:
                out.append(f"{key}={sets[key]}")
                done.add(key)
            else:
                out.append(line)
        for key, v in sets.items():
            if key not in done:
                out.append(f"{key}={v}")

        # 임시 파일에 쓴 뒤 교체 (쓰는 도중 문제가 생겨도 기존 .env 가 깨지지 않게)
        fd, tmp = tempfile.mkstemp(dir=ENV_PATH.parent, prefix=".env.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(out) + "\n")
        os.replace(tmp, ENV_PATH)

        # 실행 중인 서버에 즉시 반영
        for key, v in sets.items():
            os.environ[key] = v
        for key in removes:
            os.environ.pop(key, None)

    return sorted(set(sets) | removes)


def public_view() -> dict:
    """화면용: 비밀 값은 마스킹, 일반 값은 그대로."""
    fields = []
    for f in FIELDS:
        value = (os.getenv(f["name"]) or "").strip()
        fields.append(
            {
                **f,
                "is_set": bool(value),
                "display": _mask(value) if f["secret"] else value,
            }
        )
    return {"groups": GROUPS, "fields": fields}
