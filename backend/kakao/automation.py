"""
카카오 선물하기 브라우저 자동화 (Playwright)

[실제 사이트에서 확인한 로그인 구조 - PC 화면 기준]
  1) gift.kakao.com/home 상단의  <a class="link_login">로그인</a>  클릭
  2) shopping-userauth-web.kakao.com (중간 경유) → accounts.kakao.com (아이디/비번 입력 화면)
  3) 로그인 성공 → gift.kakao.com/a/v1/web/login → 선물하기로 복귀
  로그인 여부 확인: gift.kakao.com/a/pr/v1/reservation-box/count/reservation-pending
      로그아웃 상태면 401(AUTH_REQUIRED), 로그인 상태면 200

- 로그인 상태(쿠키)는 browser_profile/ 폴더에 저장되어 다음 실행 때 재사용
- 비밀번호는 저장하지 않음 (로그인 입력에만 사용)

주의: Playwright의 동기(sync) API는 "처음 만든 스레드"에서만 써야 합니다.
그래서 모든 브라우저 작업은 전용 스레드 1개(ThreadPoolExecutor)에서 실행합니다.
"""
import atexit
import json
import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse

from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from . import collector
from . import devtools_keys

from backend.settings import ROOT as BASE_DIR
PROFILE_DIR = BASE_DIR / "data" / "kakao_profile"   # 로그인 상태 저장 폴더 (공유 금지)
DEBUG_DIR = BASE_DIR / "data" / "kakao_debug"      # 실패 시 화면/HTML 저장 폴더

# ---- 주소 (환경변수는 테스트용. 평소에는 건드릴 필요 없음) ----
HOME_URL = os.environ.get("KAKAO_HOME_URL", "https://gift.kakao.com/home")
_u = urlparse(HOME_URL)
GIFT_ORIGIN = f"{_u.scheme}://{_u.netloc}"
GIFT_HOST = _u.hostname
AUTH_CHECK_URL = GIFT_ORIGIN + "/a/pr/v1/reservation-box/count/reservation-pending"
# 사이트의 [로그인] 링크를 못 찾을 때 직접 이동할 주소 (사이트 코드와 같은 형식)
LOGIN_URL = os.environ.get(
    "KAKAO_LOGIN_URL",
    "https://shopping-userauth-web.kakao.com/login?channel=GIFT&redirect_url="
    + quote(f"{GIFT_ORIGIN}/a/v1/web/login?redirectUrl={quote(HOME_URL, safe='')}", safe=""),
)
LOGIN_FORM_HOST = os.environ.get("KAKAO_ACCOUNTS_HOST", "accounts.kakao.com")

LOGIN_TIMEOUT = 180  # 카카오톡 승인/보안문자 등을 직접 처리할 시간(초)

# 선물하기 상단 [로그인] 링크
# 실제 소스: <a href="#none" data-tiara-layer="btn_login_PC" ... class="link_login"> 로그인 </a>
LOGIN_LINK_SELECTORS = [
    'a[data-tiara-layer="btn_login_PC"]', "a.link_login",
    'a:text-is("로그인")', 'button:text-is("로그인")',
]
# 로그인 상태에서는 같은 자리에 이름이 나옴 (실제 화면에서 확인)
#   <a href="#none" class="link_login"><span class="txt_name">홍길동</span><span class="ico_nav ico_arr">닫기</span></a>
LOGGED_IN_SELECTORS = ["a.link_login .txt_name", ".link_login .txt_name"]
# 카카오 계정 로그인 화면 (화면이 바뀔 수 있어 후보를 여러 개 둠)
ID_SELECTORS = [
    'input[name="loginId"]', 'input[name="email"]', 'input[id^="loginId"]',
    "#id_email_2", 'input[autocomplete="username"]', 'input[type="email"]',
]
PW_SELECTORS = [
    'input[name="password"]', 'input[id^="password"]', "#id_password_3", 'input[type="password"]',
]
# 상단 메뉴 [큐레이터]:  <span class="txt_lnb">큐레이터</span>
CURATOR_SELECTORS = ['span.txt_lnb:text-is("큐레이터")', 'a:has(span.txt_lnb:text-is("큐레이터"))']
# 개발자 도구처럼 'Android 크롬'으로 맞춤 (iPhone은 크롬과 정보가 어긋나 로그인 오류 가능성이 있어 제외)
DEFAULT_MOBILE_DEVICE = "Pixel 7"
MOBILE_DEVICES = ["Pixel 7", "Pixel 5", "Galaxy S9+", "Galaxy S8"]

# '로그인할 카카오계정 선택' 화면 (간편로그인 정보가 저장된 경우)
#   <span class="tit_profile">아이디@naver.com</span>
PROFILE_SELECTORS = [".list_easy .tit_profile", ".tit_profile"]
NEW_ACCOUNT_TEXT = "새로운 계정으로 로그인"
SUBMIT_SELECTORS = ["button.submit", 'button[type="submit"]', 'button:has-text("로그인")']
STAY_SELECTORS = [
    'input[name="saveSignedIn"]', 'input[name="staySignedIn"]',
    'input[id^="saveSignedIn"]', 'input[id^="staySignedIn"]',
]
# 아이디/비밀번호 오류 문구. role="alert" 는 'TIP' 같은 도움말에도 쓰여 오인식되므로 제외
ERROR_SELECTORS = [
    ".desc_error", ".txt_error", 'p[class*="error"]', 'div[class*="error"] p',
]
ERROR_IGNORE = {"TIP", "도움말", "도움말 보기"}


class LoginError(Exception):
    pass


class KakaoBrowser:
    def __init__(self, log):
        self.log = log
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="playwright")
        self._pw = None
        self._ctx = None
        self._page = None
        self._headless = None
        self._timeout = LOGIN_TIMEOUT
        self._mobile_cdp = None   # 모바일 화면 전환에 쓰는 CDP 세션 (닫으면 PC 화면으로 복귀)
        self._devtools_used = False  # F12 개발자 도구(기기 모드)를 연 적이 있는지
        self._dt_hwnd = None         # 개발자 도구를 연 크롬 창
        atexit.register(self.shutdown)

    @property
    def is_open(self):
        return self._ctx is not None

    # ------------------------------------------------------------------
    # 외부(Flask)에서 호출: 작업을 브라우저 전용 스레드에 맡김
    # ------------------------------------------------------------------
    def run(self, fn, *args, **kwargs):
        return self._executor.submit(fn, *args, **kwargs)

    def shutdown(self):
        try:
            self.run(self._shutdown).result(timeout=10)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 아래 메서드들은 모두 브라우저 전용 스레드 안에서 실행됨
    # ------------------------------------------------------------------
    def run_all(self, user_id, password, headless=False, timeout=LOGIN_TIMEOUT,
                open_curator=True, mobile_mode=True, mobile_device=DEFAULT_MOBILE_DEVICE,
                mobile_method="devtools", close_devtools=True,
                collect=False, collect_max=20, collect_interval=1.5,
                on_item=None, stop_event=None, seen=None, season=None):
        """[실행] 버튼 순서
        1) 선물하기 PC 버전 열기   2) 로그인   3) [큐레이터] 클릭   4) 모바일 버전 전환 + 새로고침
        ※ PC에서 큐레이터를 누르면 '/curator/pc-restricted'(모바일에서만 이용 가능) 화면이 나오고,
           모바일로 바꿔 새로고침하면 사이트가 실제 큐레이터 페이지(/curator)로 이동시킴"""
        if self._devtools_used and self._ctx is not None:
            # 지난 실행의 개발자 도구(모바일 모드)가 열려 있으면 PC 버전으로 시작할 수 없으므로 브라우저를 다시 염
            self.log("지난 실행의 개발자 도구(모바일 모드)를 닫기 위해 브라우저를 다시 엽니다.")
            self._close()

        for attempt in (1, 2):
            # 두 번째 시도는 '로그인된 것처럼 보였지만 모바일에서 401' 이었던 경우 → 무조건 로그인 과정을 다시 밟음
            self.login(user_id, password, headless, timeout, force=(attempt == 2))   # 1), 2)
            page = self._page
            if open_curator:
                self._open_curator(page)                       # 3)
            self._dt_hwnd = None
            if mobile_mode:                                    # 4)
                if mobile_method == "devtools" and devtools_keys.IS_WINDOWS and not self._headless:
                    self._switch_to_mobile_devtools(page)
                else:
                    if mobile_method == "devtools":
                        self.log("개발자 도구 키 입력은 Windows에서 창이 보일 때만 가능해 코드 방식으로 전환합니다.", "warn")
                    self._switch_to_mobile(page, mobile_device)
            page = self._page
            if not self._needs_login(page):
                break
            if attempt == 1:
                self.log("모바일 큐레이터 화면에서 '로그인이 필요한 페이지'가 나왔습니다. "
                         "브라우저를 다시 열고 로그인부터 다시 진행합니다.", "warn")
                self._close()  # 개발자 도구(모바일 모드)까지 정리하고 PC 화면부터 다시 시작
                continue
            raise LoginError("다시 로그인했는데도 큐레이터 화면에서 로그인이 필요하다고 나옵니다. "
                             "열린 크롬 창에서 직접 로그인한 뒤 [실행]을 다시 눌러 주세요.")
        if collect:                                        # 5) 링크 수집 (모바일 상태에서)
            self.collect_links(collect_max, collect_interval, stop_event, on_item, seen, season)
        if close_devtools and self._dt_hwnd:               # 6) 개발자 도구 닫기
            self._close_devtools(self._dt_hwnd, page)
        return True

    def collect_links(self, max_items, interval, stop_event, on_item, seen, season=None):
        """현재 열린 큐레이터 화면에서 [링크 생성]을 눌러 링크/상품 정보 수집"""
        if self._ctx is None or self._page is None or self._page.is_closed():
            raise LoginError("브라우저가 열려 있지 않습니다. 먼저 [실행]을 눌러 주세요.")
        page = self._page
        if self._needs_login(page):
            raise LoginError("'로그인이 필요한 페이지' 화면이라 링크를 수집할 수 없습니다. [실행]을 다시 눌러 로그인부터 진행하세요.")
        if not self._is_mobile_page(page):
            self.log("현재 화면이 모바일 모드가 아닙니다. 링크 생성이 PC 요청으로 처리될 수 있습니다.", "warn")
        self.log("[5] 링크 수집 시작")
        return collector.collect(
            page, self._ctx, self.log, self._sleep, int(max_items), float(interval),
            stop_event, on_item or (lambda item: None), seen if seen is not None else set(),
            GIFT_ORIGIN, self._dump, season)

    @staticmethod
    def _needs_login(page) -> bool:
        """선물하기가 '로그인이 필요한 페이지 입니다'(/error/401) 화면을 보여주는지"""
        try:
            return page is not None and not page.is_closed() and "/error/401" in page.url
        except PWError:
            return False

    def login(self, user_id, password, headless=False, timeout=LOGIN_TIMEOUT, force=False):
        """선물하기 홈 열기 → 로그인 안 돼 있으면 [로그인] 클릭 → 저장된 정보로 입력.
        force=True 면 로그인돼 보여도 로그인 과정을 다시 밟아 세션을 새로 받음"""
        self._timeout = int(timeout)
        page = self._ensure_browser(headless)
        self._reset_mobile()  # 로그인 버튼은 PC 화면에만 있으므로 PC 화면으로 되돌림

        self.log(f"[1/4] 선물하기 PC 버전 열기: {HOME_URL}")
        page.goto(HOME_URL, wait_until="domcontentloaded")
        self._settle(page)

        self.log("[2/4] 로그인 확인")
        if force:
            self.log("로그인 과정을 다시 진행합니다 (세션 새로 받기).")
        elif self._detect_logged_in(page):
            self.log("저장된 로그인 정보로 이미 로그인되어 있습니다.", "success")
            return True
        else:
            self.log("현재 로그아웃 상태입니다. 로그인을 진행합니다.")

        if not user_id or not password:
            raise LoginError("설정에 로그인 정보가 없습니다. [설정] 탭에서 아이디와 비밀번호를 저장하세요.")

        form_page = self._open_login_page(page, force)
        if form_page == "logged_in":
            # [로그인]을 눌렀더니 카카오 계정 연결로 바로 로그인된 경우
            self.log("카카오 계정 연결로 바로 로그인되었습니다.", "success")
            return True
        if form_page is not None:
            self._fill_credentials(form_page, user_id, password)
        return self._wait_for_login(form_page or page)

    def _name_visible(self, page) -> bool:
        """선물하기 상단에 로그인한 사람 이름(span.txt_name)이 보이면 로그인 상태"""
        if page is None or page.is_closed() or self._host(page) != GIFT_HOST:
            return False
        return self._first_visible(page, LOGGED_IN_SELECTORS, timeout=0) is not None

    def _detect_logged_in(self, page, timeout=10) -> bool:
        """로그인 여부 판단. 기준은 '선물하기 상단에 계정 이름이 보이는가' 하나.

        ※ 확인용 API(reservation-pending)는 실제로는 로그인이 풀렸는데도 200 을 돌려준 적이 있어
          (→ 모바일 큐레이터에서 /error/401) 그것만으로는 로그인으로 보지 않는다.
        페이지가 처음 그려질 때는 로그인 상태여도 잠깐 [로그인] 글자가 보일 수 있으므로
        [로그인] 글자가 3초 이상 그대로일 때만 로그아웃으로 확정한다."""
        start = time.time()
        link = None
        while True:
            if self._name_visible(page):
                self.log("로그인 상태 점검: 상단에 계정 이름이 보임 → 로그인 상태")
                return True
            elapsed = time.time() - start
            link = self._find_login_link(page, timeout=0)
            if link is not None and elapsed >= 3:
                self.log("로그인 상태 점검: 상단에 [로그인] 링크가 보임 → 로그아웃 상태")
                return False
            if elapsed >= timeout:
                self.log(f"로그인 상태 점검: {timeout}초 동안 계정 이름이 보이지 않아 로그아웃으로 봅니다.", "warn")
                return False
            self._sleep(0.5, page)

    def logout(self):
        """저장된 쿠키를 지우고 브라우저를 닫음"""
        if self._ctx is None:
            self._ensure_browser(headless=True)
        self._ctx.clear_cookies()
        self.log("저장된 로그인 정보(쿠키)를 삭제했습니다.")
        self._close()

    def close(self):
        """브라우저만 닫음 (로그인 상태는 유지)"""
        if self._ctx is not None:
            self._close()
            self.log("브라우저를 닫았습니다. (로그인 상태는 저장되어 있음)")

    def goto(self, url):
        """다음 단계(정보 수집)에서 사용할 페이지 이동"""
        page = self._ensure_browser(self._headless or False)
        page.goto(url, wait_until="domcontentloaded")
        self._settle(page)
        return {"url": page.url, "title": page.title()}

    # ------------------------------------------------------------------
    # 브라우저 실행/종료
    # ------------------------------------------------------------------
    def _ensure_browser(self, headless=False):
        if self._ctx is not None and self._headless != headless:
            self.log("브라우저 표시 설정이 바뀌어 다시 실행합니다.")
            self._close()

        if self._ctx is None:
            if self._pw is None:
                self._pw = sync_playwright().start()
            PROFILE_DIR.mkdir(exist_ok=True)
            opts = dict(
                user_data_dir=str(PROFILE_DIR),
                headless=headless,
                viewport=None if not headless else {"width": 1400, "height": 900},
                locale="ko-KR",
                args=["--start-maximized"],
                ignore_default_args=["--enable-automation"],  # "자동화 소프트웨어" 안내줄 제거
                # Windows: 샌드박스 사용 → '--no-sandbox 지원되지 않는 플래그' 경고줄 제거
                chromium_sandbox=(os.name == "nt"),
            )
            try:
                self._ctx = self._pw.chromium.launch_persistent_context(channel="chrome", **opts)
                self.log("PC에 설치된 Chrome으로 브라우저를 실행했습니다.")
            except PWError:
                self.log("Chrome 실행 실패 → Playwright 내장 Chromium으로 실행합니다.", "warn")
                self._ctx = self._pw.chromium.launch_persistent_context(**opts)
            self._ctx.on("close", self._on_close)
            self._ctx.on("page", self._attach_page)
            for p in self._ctx.pages:
                self._attach_page(p)
            self._headless = headless

            pages = [p for p in self._ctx.pages if not p.is_closed()]
            self._page = pages[0] if pages else self._ctx.new_page()
            if not headless:
                self._maximize(self._page)

        pages = [p for p in self._ctx.pages if not p.is_closed()]
        self._page = pages[0] if pages else self._ctx.new_page()
        return self._page

    def _maximize(self, page):
        """브라우저 창 최대화 (Chrome이 이전 창 크기를 기억해도 강제로 최대화)"""
        try:
            cdp = self._ctx.new_cdp_session(page)
            win = cdp.send("Browser.getWindowForTarget")
            wid = win["windowId"]
            cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "maximized"}})
            page.wait_for_timeout(300)
            state = cdp.send("Browser.getWindowBounds", {"windowId": wid})["bounds"]
            if state.get("windowState") != "maximized":
                # 최대화가 적용되지 않으면 화면 크기에 맞춰 창 크기를 직접 지정
                w, h, x, y = page.evaluate(
                    "[screen.availWidth, screen.availHeight, screen.availLeft || 0, screen.availTop || 0]")
                cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {
                    "windowState": "normal", "left": x, "top": y, "width": w, "height": h}})
                state = cdp.send("Browser.getWindowBounds", {"windowId": wid})["bounds"]
            cdp.detach()
            self.log(f"브라우저 창을 화면 전체 크기로 맞췄습니다 ({state.get('width')}x{state.get('height')}).")
        except PWError as e:
            self.log(f"창 최대화 실패: {e}", "warn")

    # ------------------------------------------------------------------
    # 로그인 이후 단계
    # ------------------------------------------------------------------
    def _open_curator(self, page):
        self.log("[3/4] [큐레이터] 메뉴로 이동")
        # 로그인 직후 다른 주소에 있을 수 있으므로 홈에서 메뉴를 찾음
        if self._host(page) != GIFT_HOST or self._first_visible(page, CURATOR_SELECTORS, timeout=3) is None:
            page.goto(HOME_URL, wait_until="domcontentloaded")
            self._settle(page)
        menu = self._first_visible(page, CURATOR_SELECTORS, timeout=10)
        if menu is None:
            path = self._dump(page, "no_curator_menu")
            self.log(f"[큐레이터] 메뉴를 찾지 못했습니다 (기록: {path}).", "warn")
            return False
        self.log("[큐레이터] 메뉴를 클릭합니다.")
        menu.click()
        try:
            page.wait_for_url(lambda u: "/curator" in u, timeout=10000)
        except (PWTimeout, PWError):
            pass
        self._settle(page)
        if "pc-restricted" in page.url:
            self.log("큐레이터 도착: PC에서는 '모바일에서만 이용할 수 있어요' 화면입니다 → 모바일 전환 필요")
        else:
            self.log(f"큐레이터 도착: {self._short(page.url)}")
        return True

    def _switch_to_mobile(self, page, device_name):
        """개발자 도구의 'Toggle device toolbar'(Ctrl+Shift+M) + F5 와 같은 효과.
        화면 크기 / 모바일 UA / 터치를 기기에 맞춰 바꾼 뒤 새로고침"""
        devices = self._pw.devices
        if device_name not in MOBILE_DEVICES or device_name not in devices:
            self.log(f"'{device_name}' 대신 {DEFAULT_MOBILE_DEVICE}로 전환합니다.", "warn")
            device_name = DEFAULT_MOBILE_DEVICE
        d = devices[device_name]
        vp, screen = d["viewport"], d.get("screen", d["viewport"])

        self._reset_mobile()
        ua, metadata = self._mobile_user_agent(page, d["user_agent"])
        cdp = self._ctx.new_cdp_session(page)
        cdp.send("Emulation.setDeviceMetricsOverride", {
            "width": vp["width"], "height": vp["height"],
            "deviceScaleFactor": d["device_scale_factor"], "mobile": d["is_mobile"],
            "screenWidth": screen["width"], "screenHeight": screen["height"],
        })
        # UA 문자열 + 모바일/OS 정보(Client Hints)를 함께 바꿈 → 개발자 도구 기기 모드와 동일
        cdp.send("Emulation.setUserAgentOverride",
                 {"userAgent": ua, "userAgentMetadata": metadata, "platform": "Linux armv81"})
        cdp.send("Emulation.setTouchEmulationEnabled", {"enabled": d["has_touch"], "maxTouchPoints": 5})
        cdp.send("Emulation.setEmitTouchEventsForMouse", {"enabled": True, "configuration": "mobile"})
        self._mobile_cdp = cdp

        self.log(f"[4/4] 모바일 버전으로 전환 ({device_name}, {vp['width']}x{vp['height']}) 후 새로고침(F5)")
        page.reload(wait_until="domcontentloaded")
        # 모바일로 새로고침하면 사이트가 pc-restricted → /curator 로 이동시킴
        try:
            page.wait_for_url(lambda u: "pc-restricted" not in u, timeout=10000)
        except (PWTimeout, PWError):
            pass
        self._settle(page)
        if "/error/401" in page.url:
            self.log("로그인이 필요하다는 화면이 나왔습니다. 로그인 상태를 확인하세요.", "warn")
        try:
            is_mobile_ua = page.evaluate(
                "/Mobile|Android/.test(navigator.userAgent) && !!(navigator.userAgentData && navigator.userAgentData.mobile)")
        except PWError:
            is_mobile_ua = False
        if is_mobile_ua:
            self.log(f"모바일 화면 전환 완료: {self._short(page.url)}", "success")
        else:
            self.log("모바일 화면 전환이 적용되지 않았습니다.", "warn")

    def _switch_to_mobile_devtools(self, page):
        """사람이 누르는 것과 똑같이 크롬 창에 F12 → Ctrl+Shift+M → F5 키 입력"""
        self.log("[4/4] 개발자 도구로 모바일 버전 전환: F12 → Ctrl+Shift+M → F5")

        # 크롬 창 찾기: 탭 제목을 잠시 고유한 값으로 바꿔 창 제목으로 찾음
        page.bring_to_front()
        marker = f"EK-{uuid.uuid4().hex[:8]}"
        old_title = page.title()
        page.evaluate("t => { document.title = t }", marker)
        hwnd = None
        for _ in range(15):
            hwnd = devtools_keys.find_window(marker)
            if hwnd:
                break
            self._sleep(0.3, page)
        try:
            page.evaluate("t => { document.title = t }", old_title)
        except PWError:
            pass
        if not hwnd:
            self.log("크롬 창을 찾지 못했습니다. 직접 F12 → Ctrl+Shift+M → F5 를 눌러 주세요.", "warn")
            return False

        if not self._bring_to_front(hwnd, page):
            return False

        self._devtools_used = True
        devtools_keys.press("F12")
        self.log("F12: 개발자 도구 열기")
        self._sleep(2.5, page)

        # 개발자 도구는 '기기 모드 켜짐'을 기억함 → 이미 켜져 있으면 Ctrl+Shift+M을 누르면 오히려 꺼짐
        already_mobile = self._is_mobile_page(page)
        for attempt in (1, 2):
            if not devtools_keys.is_foreground(hwnd) and not self._bring_to_front(hwnd, page):
                return False
            if attempt == 1 and already_mobile:
                self.log("개발자 도구가 이미 모바일 기기 모드라 Ctrl+Shift+M은 생략합니다.")
            else:
                devtools_keys.press("CTRL", "SHIFT", "M")
                self.log("Ctrl+Shift+M: Toggle device toolbar")
                self._sleep(1.2, page)
            try:
                with page.expect_event("load", timeout=15000):
                    devtools_keys.press("F5")
                    self.log("F5: 새로고침")
            except (PWTimeout, PWError):
                pass
            try:
                page.wait_for_url(lambda u: "pc-restricted" not in u, timeout=10000)
            except (PWTimeout, PWError):
                pass
            self._settle(page)

            if self._is_mobile_page(page):
                self.log(f"모바일 버전 전환 완료: {self._short(page.url)}", "success")
                if "/error/401" in page.url:
                    self.log("로그인이 필요하다는 화면이 나왔습니다.", "warn")
                self._dt_hwnd = hwnd  # 수집 후 개발자 도구를 닫을 때 사용
                return True
            if attempt == 1:
                # 개발자 도구가 '기기 모드 켜짐'을 기억하고 있으면 방금 키로 꺼졌을 수 있음 → 한 번 더
                self.log("모바일 모드가 아니어서 Ctrl+Shift+M을 한 번 더 누릅니다.", "warn")

        self.log("모바일 버전으로 바뀌지 않았습니다. 크롬 창에서 직접 Ctrl+Shift+M → F5 를 눌러 주세요.", "warn")
        return False

    def _close_devtools(self, hwnd, page):
        """F12를 한 번 더 눌러 개발자 도구 창 닫기.
        (닫으면 기기 모드도 꺼지지만, 이미 열린 모바일 페이지는 그대로 남음)"""
        for attempt in (1, 2):
            if not devtools_keys.is_foreground(hwnd) and not self._bring_to_front(hwnd, page):
                return False
            devtools_keys.press("F12")
            self.log("F12: 개발자 도구 닫기")
            self._sleep(1.5, page)
            # 개발자 도구가 닫히면 기기 모드가 풀려 브라우저가 다시 PC로 인식됨 → 닫힘 확인 용도
            if not self._is_mobile_page(page):
                self._devtools_used = False  # 닫혔으므로 다음 실행 때 브라우저를 다시 열 필요 없음
                self.log(f"개발자 도구를 닫았습니다. 현재 화면: {self._short(page.url)}", "success")
                self.log("※ 이 화면에서 새로고침하거나 다른 페이지로 이동하면 다시 PC 버전으로 열립니다.")
                return True
        self.log("개발자 도구가 닫히지 않았습니다. 크롬 창에서 F12를 눌러 닫아 주세요.", "warn")
        return False

    def _bring_to_front(self, hwnd, page):
        if devtools_keys.focus_window(hwnd):
            return True
        self.log("크롬 창을 앞으로 가져오지 못했습니다. 크롬 창을 한 번 클릭해 주세요 (15초 대기).", "warn")
        deadline = time.time() + 15
        while time.time() < deadline:
            if devtools_keys.is_foreground(hwnd):
                return True
            self._sleep(0.5, page)
        self.log("크롬 창이 선택되지 않아 키 입력을 하지 못했습니다.", "error")
        return False

    def _is_mobile_page(self, page):
        try:
            return page.evaluate(
                "!!(navigator.userAgentData && navigator.userAgentData.mobile)"
                " || /Mobile|Android|iPhone/.test(navigator.userAgent)")
        except PWError:
            return False

    def _mobile_user_agent(self, page, device_ua):
        """실행 중인 크롬의 실제 버전으로 Android 모바일 UA와 Client Hints 정보를 만듦"""
        info = page.evaluate("""async () => {
            const d = navigator.userAgentData;
            const hi = d ? await d.getHighEntropyValues(['fullVersionList', 'uaFullVersion']) : {};
            return {ua: navigator.userAgent, brands: d ? d.brands : [],
                    fullVersionList: hi.fullVersionList || [], full: hi.uaFullVersion || ''};
        }""")
        m = re.search(r"Chrome/([\d.]+)", info["ua"])
        full = info["full"] or (m.group(1) if m else "120.0.0.0")
        dm = re.search(r"Android ([\d.]+); ([^;)]+?)(?: Build/[^)]*)?\)", device_ua)
        android, model = (dm.group(1), dm.group(2).strip()) if dm else ("14", "Pixel 7")
        major = full.split(".")[0]
        ua = (f"Mozilla/5.0 (Linux; Android {android}; {model}) AppleWebKit/537.36 "
              f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Mobile Safari/537.36")
        metadata = {
            "brands": info["brands"] or [{"brand": "Chromium", "version": major}],
            "fullVersionList": info["fullVersionList"] or [{"brand": "Chromium", "version": full}],
            "fullVersion": full,
            "platform": "Android", "platformVersion": android,
            "architecture": "", "model": model, "mobile": True,
        }
        return ua, metadata

    def _reset_mobile(self):
        """모바일 화면 전환 해제 (CDP 세션을 닫으면 설정이 원래대로 돌아감)"""
        if self._mobile_cdp is not None:
            try:
                self._mobile_cdp.detach()
            except PWError:
                pass
            self._mobile_cdp = None

    def _attach_page(self, page):
        # "로그인하시겠습니까?" 같은 확인창은 자동으로 [확인], 그 외 창은 [취소]
        def on_dialog(d):
            try:
                d.accept() if "로그인" in d.message else d.dismiss()
            except PWError:
                pass
        page.on("dialog", on_dialog)

    def _on_close(self, *_):
        if self._ctx is not None:
            self.log("브라우저 창이 닫혔습니다.", "warn")
        self._ctx = None
        self._page = None

    def _close(self):
        self._mobile_cdp = None
        self._devtools_used = False
        ctx, self._ctx, self._page = self._ctx, None, None
        if ctx is not None:
            try:
                ctx.close()
            except PWError:
                pass

    def _shutdown(self):
        self._close()
        if self._pw is not None:
            self._pw.stop()
            self._pw = None

    # ------------------------------------------------------------------
    # 로그인 단계
    # ------------------------------------------------------------------
    def _open_login_page(self, page, force=False):
        """[로그인] 링크 클릭 → 카카오 계정 로그인 화면(accounts.kakao.com) 페이지 반환.
        로그인 화면 없이 선물하기로 바로 돌아와 로그인된 경우 "logged_in" 반환.
        force=True 면 상단에 이름이 보여도 로그인 주소로 직접 이동해 세션을 새로 받음"""
        link = self._find_login_link(page, timeout=5)
        if link is not None:
            self.log("선물하기 상단의 [로그인]을 클릭합니다.")
            link.click()
            form = self._wait_for_host(LOGIN_FORM_HOST, timeout=20)
            if form is not None:
                return form
            self.log(f"로그인 화면으로 넘어가지 않았습니다 (현재: {self._short(page.url)}).", "warn")
        elif not force:
            self.log("[로그인] 링크를 찾지 못했습니다.", "warn")

        if not force and self._detect_logged_in(page, timeout=5):
            return "logged_in"

        self.log("카카오 로그인 주소로 직접 이동합니다.")
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        form = self._wait_for_host(LOGIN_FORM_HOST, timeout=20)
        if form is not None:
            return form
        if self._detect_logged_in(page, timeout=5):
            return "logged_in"

        path = self._dump(page, "no_login_page")
        self.log(f"카카오 로그인 화면을 찾지 못했습니다 (현재: {self._short(page.url)}, 기록: {path}). "
                 "열린 브라우저 창에서 직접 로그인해 주세요.", "warn")
        return None

    def _wait_for_host(self, host, timeout):
        """열린 창(팝업 포함) 중 주소가 host 인 페이지가 나타날 때까지 대기.
        그 사이 선물하기 화면에 계정 이름이 뜨면(이미 로그인) 기다리지 않고 None 반환"""
        start = time.time()
        deadline = start + timeout
        while time.time() < deadline:
            if self._ctx is None:
                raise LoginError("브라우저 창이 닫혔습니다.")
            for p in self._ctx.pages:
                if not p.is_closed() and self._host(p) == host:
                    self._settle(p)
                    self.log(f"카카오 로그인 화면 도착: {self._short(p.url)}")
                    return p
            if time.time() - start > 3 and any(self._name_visible(p) for p in self._ctx.pages):
                return None
            self._sleep(0.3)
        return None

    def _detect_login_screen(self, page, timeout=10):
        """카카오 로그인 화면 종류 판별: 'chooser'(계정 선택) / 'form'(아이디 입력) / None"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._first_visible(page, PROFILE_SELECTORS, timeout=0) is not None:
                return "chooser"
            if self._first_visible(page, ID_SELECTORS, timeout=0) is not None:
                return "form"
            self._sleep(0.3, page)
        return None

    def _choose_account(self, page, user_id):
        """계정 선택 화면에서 설정된 아이디와 같은 계정을 클릭. 성공 시 True"""
        profiles = page.locator(", ".join(PROFILE_SELECTORS))
        names = []
        for i in range(min(profiles.count(), 10)):
            el = profiles.nth(i)
            try:
                name = el.inner_text(timeout=1000).strip()
            except PWError:
                continue
            names.append(name)
            if name.lower() == user_id.strip().lower() and el.is_visible():
                self.log(f"'로그인할 카카오계정 선택' 화면 → {name} 클릭")
                el.click()
                return True

        self.log(f"계정 선택 목록에 설정된 아이디가 없습니다 (목록: {', '.join(names) or '없음'}). "
                 f"[{NEW_ACCOUNT_TEXT}]을 누릅니다.", "warn")
        try:
            page.get_by_text(NEW_ACCOUNT_TEXT).first.click()
        except PWError:
            self.log(f"[{NEW_ACCOUNT_TEXT}] 버튼을 찾지 못했습니다. 브라우저 창에서 직접 진행해 주세요.", "warn")
        return False

    def _fill_credentials(self, page, user_id, password):
        screen = self._detect_login_screen(page)

        if screen == "chooser":
            if self._choose_account(page, user_id):
                # 계정 클릭 후: 바로 로그인되거나, 비밀번호만 다시 묻는 화면이 나올 수 있음
                pw_box = self._first_visible(page, PW_SELECTORS, timeout=6)
                if pw_box is not None and self._host(page) == LOGIN_FORM_HOST:
                    self.log("비밀번호 입력 화면 → 저장된 비밀번호를 입력합니다.")
                    pw_box.click()
                    pw_box.fill("")
                    pw_box.press_sequentially(password, delay=50)
                    self._submit(page, pw_box)
                return
            screen = self._detect_login_screen(page)  # [새로운 계정으로 로그인] 이후 화면

        id_box = self._first_visible(page, ID_SELECTORS, timeout=10) if screen == "form" else None
        if id_box is None:
            path = self._dump(page, "no_id_input")
            self.log(f"아이디 입력칸을 찾지 못했습니다(기록: {path}). "
                     "열린 브라우저 창에서 직접 로그인해 주세요.", "warn")
            return
        id_box.click()
        id_box.fill("")
        id_box.press_sequentially(user_id, delay=50)

        pw_box = self._first_visible(page, PW_SELECTORS, timeout=5)
        if pw_box is None:
            path = self._dump(page, "no_pw_input")
            self.log(f"비밀번호 입력칸을 찾지 못했습니다(기록: {path}). 브라우저 창에서 직접 진행해 주세요.", "warn")
            return
        pw_box.click()
        pw_box.press_sequentially(password, delay=50)

        self._check_stay_signed_in(page)
        self._submit(page, pw_box)
        self.log("아이디/비밀번호를 입력하고 로그인을 요청했습니다.")

    def _submit(self, page, pw_box):
        submit = self._first_visible(page, SUBMIT_SELECTORS, timeout=3)
        if submit is not None:
            submit.click()
        else:
            pw_box.press("Enter")

    def _check_stay_signed_in(self, page):
        """'로그인 상태 유지' 체크 → 브라우저를 껐다 켜도 로그인 유지"""
        for sel in STAY_SELECTORS:
            try:
                box = page.locator(sel).first
                if box.count():
                    if not box.is_checked():
                        box.check(force=True)  # 체크박스가 디자인상 숨겨진 경우 대비
                    self.log("'로그인 상태 유지'를 체크했습니다.")
                    return
            except PWError:
                pass
        try:
            box = page.get_by_label("로그인 상태 유지")
            if box.count():
                if not box.first.is_checked():
                    box.first.check(force=True)
                self.log("'로그인 상태 유지'를 체크했습니다.")
                return
        except PWError:
            pass
        self.log("'로그인 상태 유지' 체크박스를 찾지 못했습니다. "
                 "브라우저를 닫으면 다시 로그인해야 할 수 있습니다.", "warn")

    def _wait_for_login(self, page):
        start = time.time()
        deadline = start + self._timeout
        next_notice = start + 8
        next_check = 0
        reclicked = False

        while time.time() < deadline:
            if self._ctx is None:
                raise LoginError("로그인 도중 브라우저 창이 닫혔습니다.")
            if page.is_closed():  # 팝업 로그인 창이 닫힌 경우 → 남은 창 확인
                pages = [p for p in self._ctx.pages if not p.is_closed()]
                if not pages:
                    raise LoginError("브라우저 창이 모두 닫혔습니다.")
                page = pages[0]

            host = self._host(page)

            # 아이디/비밀번호 오류 문구
            if host == LOGIN_FORM_HOST:
                err = self._error_text(page)
                if err:
                    path = self._dump(page, "login_error")
                    raise LoginError(f"{err} (기록: {path})")

            # 선물하기 상단에 계정 이름이 보이면 바로 성공
            if host == GIFT_HOST and self._name_visible(page):
                self._page = page
                self.log("로그인 성공! (선물하기 상단에 계정 이름 확인)", "success")
                return True

            # 2초마다 실제 로그인 여부 확인 (선물하기 API)
            if time.time() >= next_check:
                next_check = time.time() + 2
                if (host == GIFT_HOST and self._login_state() is True
                        and self._find_login_link(page, timeout=0) is None):
                    self._page = page
                    self.log("로그인 성공! (선물하기 로그인 확인 완료)", "success")
                    return True

            # 선물하기로 돌아왔는데 로그인 안 된 경우 → [로그인] 한 번 더 클릭 (카카오 계정 자동 연결)
            if host == GIFT_HOST and not reclicked and time.time() - start > 10:
                link = self._find_login_link(page, timeout=0)
                if link is not None:
                    reclicked = True
                    self.log("선물하기에 아직 로그인되지 않아 [로그인]을 한 번 더 클릭합니다.")
                    link.click()

            if time.time() >= next_notice and host != GIFT_HOST:
                left = int(deadline - time.time())
                self.log("추가 확인이 필요할 수 있습니다. 브라우저 창을 확인해 주세요 "
                         f"(카카오톡 승인 / 보안문자 / 동의 화면 등). 남은 시간 {left}초", "warn")
                next_notice = time.time() + 30
            self._sleep(0.5, page)

        path = self._dump(page, "login_timeout")
        raise LoginError(f"{self._timeout}초 안에 로그인이 완료되지 않았습니다. "
                         f"(마지막 주소: {self._short(page.url)}, 기록: {path})")

    # ------------------------------------------------------------------
    # 판별/보조 함수
    # ------------------------------------------------------------------
    def _login_state(self):
        """선물하기 API로 로그인 여부 확인: True(로그인) / False(로그아웃) / None(확인 불가)"""
        try:
            r = self._ctx.request.get(
                AUTH_CHECK_URL, timeout=10000,
                headers={"Accept": "application/json", "Referer": HOME_URL},
            )
            if r.status == 200:
                return True
            if r.status in (401, 403):
                return False
            self.log(f"로그인 확인 API 응답 {r.status}", "warn")
        except PWError as e:
            self.log(f"로그인 확인 API 호출 실패: {e}", "warn")
        return None

    def _find_login_link(self, page, timeout):
        """상단 [로그인] 링크 찾기. 로그인 상태에서는 같은 자리(class="link_login")에
        '로그아웃'이 나오므로 글자가 정확히 '로그인'인 것만 인정"""
        deadline = time.time() + timeout
        while True:
            for sel in LOGIN_LINK_SELECTORS:
                try:
                    loc = page.locator(sel)
                    for i in range(min(loc.count(), 3)):
                        el = loc.nth(i)
                        if el.is_visible() and el.inner_text(timeout=1000).strip() == "로그인":
                            return el
                except PWError:
                    pass
            if time.time() >= deadline:
                return None
            self._sleep(0.3, page)

    def _first_visible(self, page, selectors, timeout):
        deadline = time.time() + timeout
        while True:
            for sel in selectors:
                try:
                    loc = page.locator(sel)
                    for i in range(min(loc.count(), 3)):
                        if loc.nth(i).is_visible():
                            return loc.nth(i)
                except PWError:
                    pass
            if time.time() >= deadline:
                return None
            self._sleep(0.3, page)

    def _error_text(self, page):
        for sel in ERROR_SELECTORS:
            try:
                loc = page.locator(sel)
                for i in range(min(loc.count(), 3)):
                    el = loc.nth(i)
                    if el.is_visible():
                        text = el.inner_text(timeout=1000).strip()
                        if 2 <= len(text) <= 200 and text not in ERROR_IGNORE:
                            return text
            except PWError:
                pass
        return None

    def _sleep(self, sec, page=None):
        """대기하면서 브라우저 이벤트(주소 변경, 새 창 등)를 계속 처리.
        sync Playwright에서 time.sleep()을 쓰면 이벤트가 멈춰 page.url이 갱신되지 않음"""
        if page is None or page.is_closed():
            pages = [p for p in self._ctx.pages if not p.is_closed()] if self._ctx else []
            page = pages[0] if pages else None
        if page is None:
            time.sleep(sec)
            return
        try:
            page.wait_for_timeout(sec * 1000)
        except PWError:
            time.sleep(sec)

    def _settle(self, page):
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except (PWTimeout, PWError):
            pass

    @staticmethod
    def _host(page):
        try:
            return urlparse(page.url).hostname or ""
        except PWError:
            return ""

    @staticmethod
    def _short(url):
        return url.split("?")[0]

    def _dump(self, page, name):
        """문제 분석용: 화면 캡처 + HTML + 입력칸 목록 저장 (비밀번호 값은 저장 안 함)"""
        try:
            DEBUG_DIR.mkdir(exist_ok=True)
            base = DEBUG_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{name}"
            page.screenshot(path=f"{base}.png")
            Path(f"{base}.html").write_text(page.content(), encoding="utf-8")
            fields = page.evaluate("""() => [...document.querySelectorAll('input,button,a.link_login')]
                .map(e => ({tag: e.tagName, type: e.type, name: e.name, id: e.id,
                            cls: String(e.className).slice(0, 60), placeholder: e.placeholder,
                            text: (e.innerText || '').trim().slice(0, 30),
                            visible: !!(e.offsetWidth || e.offsetHeight)}))""")
            Path(f"{base}_fields.json").write_text(
                json.dumps({"url": page.url, "fields": fields}, ensure_ascii=False, indent=1),
                encoding="utf-8")
            return f"data/kakao_debug/{base.name}.*"
        except PWError:
            return "저장 실패"
