"""쿠팡 파트너스 Open API 클라이언트 (HMAC 서명). 골드박스·검색·딥링크·리포트가 공통으로 사용."""

import hashlib
import hmac
import time
from urllib.parse import urlencode

import httpx

DOMAIN = "https://api-gateway.coupang.com"
BASE_PATH = "/v2/providers/affiliate_open_api/apis/openapi/v1"


class CoupangClient:
    def __init__(self, access_key: str, secret_key: str, timeout: float = 10):
        self.access_key = access_key
        self.secret_key = secret_key
        self.timeout = timeout

    def _authorization(self, method: str, path: str, query: str) -> str:
        signed_date = time.strftime("%y%m%dT%H%M%SZ", time.gmtime())
        message = signed_date + method + path + query
        signature = hmac.new(self.secret_key.encode(), message.encode(), hashlib.sha256).hexdigest()
        return (
            f"CEA algorithm=HmacSHA256, access-key={self.access_key}, "
            f"signed-date={signed_date}, signature={signature}"
        )

    def request(self, method: str, endpoint: str, params: dict | None = None, json: dict | None = None):
        path = BASE_PATH + endpoint
        query = urlencode(params or {})
        url = DOMAIN + path + (f"?{query}" if query else "")
        headers = {
            "Authorization": self._authorization(method, path, query),
            "Content-Type": "application/json;charset=UTF-8",
        }
        resp = httpx.request(method, url, headers=headers, json=json, timeout=self.timeout)
        if resp.status_code != 200:
            raise RuntimeError(f"쿠팡 API 오류 {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        if str(body.get("rCode")) != "0":
            raise RuntimeError(f"쿠팡 API 오류 rCode={body.get('rCode')}: {body.get('rMessage')}")
        return body.get("data")

    def goldbox(self, sub_id: str | None = None) -> list[dict]:
        return self.request("GET", "/products/goldbox", {"subId": sub_id} if sub_id else None) or []
