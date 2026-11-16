"""
큐레이터 페이지의 상품별 [링크 생성] 버튼을 눌러 공유 링크 + 상품 정보(사진/브랜드/상품명/가격) 수집

  <button type="button" class="btn_prd" data-tiara-area-code="multi.product_group.exclusive.btn_link"> 링크 생성 </button>

[링크 생성]을 누른 뒤 사이트 동작을 모르므로 아래 경우를 모두 처리:
  1) 클립보드에 바로 복사 (실제 사이트 동작) → navigator.clipboard / execCommand('copy') 를 가로채서 저장
     가로채기가 안 되면 Windows 클립보드를 직접 읽어 클릭 전과 비교
  2) 링크 + [복사] 버튼이 있는 팝업 → [복사]를 눌러 가로채거나, 팝업 글자에서 링크 읽기
  3) 휴대폰 공유창(navigator.share) → 공유창 대신 내용을 저장
  4) 위에서 못 찾으면 → 그 순간 받은 서버 응답에서 링크 찾기 (확인 필요 표시)
"""
import re
import time
from datetime import datetime
from urllib.parse import urljoin

from playwright.sync_api import Error as PWError

from . import devtools_keys

LINK_BUTTON_CSS = 'button.btn_prd, button[data-tiara-area-code$="btn_link"]'
URL_RE = re.compile(r"https?://[^\s\"'<>]+")

# 페이지 안에 '복사/공유' 가로채기 설치
HOOK_JS = r"""() => {
  if (window.__ekHooked) return;
  window.__ekHooked = true;
  window.__ekCopied = [];
  const push = (v) => { try { if (v) window.__ekCopied.push(String(v)); } catch (e) {} };
  try {
    if (navigator.clipboard) {
      const wt = navigator.clipboard.writeText ? navigator.clipboard.writeText.bind(navigator.clipboard) : null;
      navigator.clipboard.writeText = (t) => { push(t); return wt ? wt(t).catch(() => {}) : Promise.resolve(); };
      const w = navigator.clipboard.write ? navigator.clipboard.write.bind(navigator.clipboard) : null;
      navigator.clipboard.write = async (items) => {
        try { for (const it of items) for (const ty of it.types)
                if (ty === 'text/plain') push(await (await it.getType(ty)).text()); } catch (e) {}
        return w ? w(items).catch(() => {}) : undefined;
      };
    }
  } catch (e) {}
  const ex = document.execCommand.bind(document);
  document.execCommand = (cmd, ...a) => {
    if (String(cmd).toLowerCase() === 'copy') {
      let t = (document.getSelection() || '').toString();
      const ae = document.activeElement;
      if (!t && ae && 'value' in ae) t = ae.value;
      push(t);
    }
    return ex(cmd, ...a);
  };
  document.addEventListener('copy', (e) => {
    try { const d = e.clipboardData && e.clipboardData.getData('text/plain'); if (d) push(d); } catch (_) {}
  }, true);
  navigator.share = (data) => { push(data ? [data.text, data.url].filter(Boolean).join('\n') : ''); return Promise.resolve(); };
  navigator.canShare = () => true;
}"""

# 버튼이 속한 상품 카드에서 사진/글자/상품 링크 추출
CARD_JS = r"""(btn) => {
  const SEL = 'button.btn_prd, button[data-tiara-area-code$="btn_link"]';
  const count = (el) => el.querySelectorAll(SEL).length;
  let card = btn;
  while (card.parentElement && card.parentElement !== document.body && count(card.parentElement) <= 1)
    card = card.parentElement;
  const imgs = [...card.querySelectorAll('img')].map(i => ({
      src: i.currentSrc || i.src || i.getAttribute('data-src') || '',
      area: (i.naturalWidth || i.width || 0) * (i.naturalHeight || i.height || 0), alt: i.alt || ''}))
    .filter(i => i.src && !i.src.startsWith('data:'));
  imgs.sort((a, b) => b.area - a.area);
  const lines = (card.innerText || '').split('\n').map(s => s.trim())
    .filter(s => s && s !== '링크 생성');
  return {image: imgs.length ? imgs[0].src : '', alt: imgs.length ? imgs[0].alt : '',
          lines, hrefs: [...card.querySelectorAll('a[href]')].map(a => a.href)};
}"""

# 클릭 전: 이미 떠 있는 레이어 표시 (클릭 후 새로 뜬 팝업만 보기 위해)
MARK_BASE_JS = r"""() => {
  document.querySelectorAll('[data-ek-base]').forEach(e => e.removeAttribute('data-ek-base'));
  document.querySelectorAll(__SEL__).forEach(e => e.setAttribute('data-ek-base', '1'));
}"""
# 클릭 후: 새로 뜬 팝업/토스트에서 링크, [복사], [닫기] 찾기
DIALOG_JS = r"""() => {
  const vis = (e) => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
                       return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  document.querySelectorAll('[data-ek-copy],[data-ek-close]').forEach(e => {
    e.removeAttribute('data-ek-copy'); e.removeAttribute('data-ek-close'); });
  const boxes = [...document.querySelectorAll(__SEL__)].filter(e => !e.hasAttribute('data-ek-base') && vis(e));
  const urls = [], texts = []; let hasCopy = false, hasClose = false;
  for (const b of boxes) {
    const t = (b.innerText || '').trim();
    if (t) texts.push(t.slice(0, 200));
    (t.match(/https?:\/\/[^\s"'<>]+/g) || []).forEach(u => urls.push(u));
    b.querySelectorAll('input, textarea').forEach(i => { if (/^https?:\/\//.test(i.value || '')) urls.push(i.value); });
    const btns = [...b.querySelectorAll('button, a, [role=button]')].filter(vis);
    const cp = btns.find(x => /복사/.test(x.innerText || x.getAttribute('aria-label') || ''));
    if (cp && !hasCopy) { cp.setAttribute('data-ek-copy', '1'); hasCopy = true; }
    const cl = btns.find(x => /닫기|확인|취소|close/i.test((x.innerText || '') + (x.getAttribute('aria-label') || '') + x.className));
    if (cl && !hasClose) { cl.setAttribute('data-ek-close', '1'); hasClose = true; }
  }
  return {urls, texts, hasCopy, hasClose};
}"""
LAYER_SEL = ("'[role=dialog],[role=alertdialog],[aria-modal=true],[class*=layer],[class*=Layer],"
             "[class*=popup],[class*=Popup],[class*=modal],[class*=Modal],[class*=sheet],[class*=Sheet],"
             "[class*=toast],[class*=Toast]'")
MARK_BASE_JS = MARK_BASE_JS.replace("__SEL__", LAYER_SEL)
DIALOG_JS = DIALOG_JS.replace("__SEL__", LAYER_SEL)

NOISE_URL = re.compile(r"kakaocdn|\.(js|css|png|jpe?g|gif|webp|svg|woff2?)(\?|$)|tiara|stat\.|kcl\.kakao|onkakao", re.I)


def _num(pattern, text):
    m = re.search(pattern, text)
    return m.group(1) if m else ""


# 상품 카드 안의 안내 문구 (상품명·브랜드로 잘못 읽히지 않게 제외)
NOISE_LINES = {"링크 생성", "링크 복사", "링크를 복사하고 공유해보세요"}
# 화면 낭독기용 문구 (예: ", 상품명 :", ", 브랜드명 : 카카올라")
A11Y_LINE = re.compile(r"(상품명|브랜드명)\s*:")
# 가격 줄 (예: "15,900원", "최대혜택가 14,900원")
PRICE_LINE = re.compile(r"\d[\d,]*\s*원")


def clean_brand(brand: str) -> str:
    """브랜드 값 정리: 안내 문구·접근성 문구면 빈 값"""
    brand = (brand or "").replace(">", "").replace("›", "").strip()
    if not brand or brand in NOISE_LINES or brand.startswith(",") or A11Y_LINE.search(brand):
        return ""
    return brand


def parse_card(card, page_url):
    """카드 글자 → 브랜드/상품명/할인율/가격/최대혜택가/뱃지"""
    lines = [l for l in card["lines"] if l not in NOISE_LINES]
    text = "\n".join(lines)
    badge = next((l for l in lines if re.search(r"최근\s*구매|[\d,]+\s*명", l)), "")
    benefit = _num(r"최대\s*혜택가\s*([\d,]+)\s*원", text)
    discount = _num(r"(\d{1,3})\s*%", text)
    price = ""
    for l in lines:
        if "혜택" in l:
            continue
        p = _num(r"([\d,]+)\s*원", l)
        if p:
            price = p
            break
    skip = {badge}
    # 접근성용 문구 ", 상품명 : OOO" / ", 브랜드명 : OOO" 는 상품명·브랜드 후보에서 빼고 힌트로만 사용
    brand_hint = _num(r"브랜드명\s*:\s*(.+)", text).strip()
    # 가격 줄('15,900원', '최대혜택가 14,900원')만 빼야 함. 예전처럼 '원' 글자로 거르면
    # '수능응원', '합격기원' 같은 상품명까지 빠져서 숫자+원 형태만 가격으로 봄
    cands = [l for l in lines if l not in skip and not PRICE_LINE.search(l) and not re.fullmatch(r"\d{1,3}\s*%", l)
             and not A11Y_LINE.search(l)]
    name = max(cands, key=len) if cands else ""
    brand = ""
    if name in lines:
        i = lines.index(name)
        if i > 0 and lines[i - 1] not in skip and len(lines[i - 1]) <= 30 and not A11Y_LINE.search(lines[i - 1]):
            brand = lines[i - 1]
    brand = clean_brand(brand) or clean_brand(brand_hint)

    product_url = next((h for h in card["hrefs"] if re.search(r"/product/\d+", h)), "")
    product_id = _num(r"/product/(\d+)", product_url)
    return {
        "brand": brand, "name": name, "discount": f"{discount}%" if discount else "",
        "price": f"{price}원" if price else "", "benefit": f"{benefit}원" if benefit else "",
        "badge": badge, "image": urljoin(page_url, card["image"]) if card["image"] else "",
        "product_url": product_url, "product_id": product_id,
    }


class _TooManyFails(Exception):
    pass


def _stopped(stop_event) -> bool:
    return stop_event is not None and stop_event.is_set()


def _make_prefer(keywords: list[str]):
    """상품 카드가 어떤 시즌 키워드와 맞는지 (2단계 매칭과 같은 규칙)"""
    from backend.affiliates.product_search import KakaoProductSearch

    def prefer(info: dict) -> str | None:
        for kw in keywords:
            if KakaoProductSearch.match_score(kw, info.get("name") or "", info.get("brand") or ""):
                return kw
        return None
    return prefer


def collect(page, ctx, log, sleep, max_items, interval, stop_event, on_item, seen, origin, dump, season=None):
    """현재 페이지의 [링크 생성/복사] 버튼들을 차례로 눌러 수집. 수집 개수 반환.

    season = {"keywords": [...], "method": "search" | "scan", "per_search": 5} 이면 시즌 상품을 먼저 모은다.
      - search: 큐레이터 화면의 검색창에 시즌 키워드를 하나씩 넣고, 검색 결과에서 키워드당 per_search 개까지 수집
                (검색창을 못 찾으면 scan 으로 자동 전환)
      - scan  : 목록 끝까지 훑으면서 상품명이 시즌 키워드와 맞는 상품만 수집
    그다음 개수가 남으면 목록 맨 위부터 나머지 상품으로 채운다.
    """
    try:
        ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=origin)
    except PWError:
        pass
    page.evaluate(HOOK_JS)

    # 검색 화면으로 바뀌어도 숨겨진 목록이 페이지에 남아 있을 수 있어 '보이는' 버튼만 대상으로 함
    buttons = page.locator(LINK_BUTTON_CSS).filter(has_text="링크").filter(visible=True)
    log(f"[링크 수집] 시작: 화면의 링크 버튼 {buttons.count()}개 발견 (최대 {max_items}개 수집)")

    keywords = list((season or {}).get("keywords") or [])
    method = (season or {}).get("method") or "search"
    per_search = int((season or {}).get("per_search") or 5)
    state = {"count": 0, "fails": 0}
    try:
        if keywords and method == "search":
            log(f"[링크 수집] 1차: 큐레이터 검색창에 시즌 키워드 {len(keywords)}개를 넣어 키워드당 최대 {per_search}개씩 수집합니다.")
            if not _collect_by_search(page, buttons, log, sleep, max_items, interval, stop_event, on_item, seen,
                                      dump, state, keywords, per_search):
                method = "scan"
        if keywords and method == "scan" and state["count"] < max_items and not _stopped(stop_event):
            log("[링크 수집] 1차: 목록을 훑으면서 시즌 키워드와 맞는 상품부터 수집합니다.")
            _collect_pass(page, buttons, log, sleep, max_items, interval, stop_event, on_item, seen, dump,
                          state, prefer=_make_prefer(keywords))
        if keywords and state["count"] < max_items and not _stopped(stop_event):
            log(f"[링크 수집] 2차: 시즌 상품 {state['count']}개 수집 → 남은 {max_items - state['count']}개는 "
                "나머지 상품으로 채웁니다.")
            page.evaluate("window.scrollTo(0, 0)")
            sleep(1.0, page)
        if state["count"] < max_items and not _stopped(stop_event):
            _collect_pass(page, buttons, log, sleep, max_items, interval, stop_event, on_item, seen, dump, state)
    except _TooManyFails:
        pass

    log(f"[링크 수집] 완료: {state['count']}개", "success")
    return state["count"]


# ---------- 큐레이터 검색창 ----------
# 실제 화면(확인됨):
#   큐레이터 목록 상단의 '큐레이터 상품을 검색해보세요' 는 누르면 검색 화면이 열리는 상자이고,
#   실제 입력칸은 <input type="search" id="searchInput" placeholder="검색어를 입력해주세요."> (Angular)
SEARCH_INPUTS = ["#searchInput", 'input[type="search"]']
SEARCH_TRIGGERS = [
    ':text("큐레이터 상품을 검색해보세요")', '[placeholder*="큐레이터 상품"]', 'input[placeholder*="검색"]',
    'button[aria-label*="검색"]', 'a[aria-label*="검색"]', '[class*="search"]',
]
ACTION_TIMEOUT = 5000  # 검색 관련 동작은 5초 안에 안 되면 포기 (기본 30초 대기로 멈춰 보이던 문제)
# 검색 상자를 누르면 열리는 큐레이터 전용 검색 화면 (실제 화면 기록으로 확인)
CURATOR_SEARCH_PATH = "/search?inboundTag=curator"


def _first_visible(page, selector):
    try:
        loc = page.locator(selector)
        for i in range(min(loc.count(), 5)):
            if loc.nth(i).is_visible():
                return loc.nth(i)
    except PWError:
        pass
    return None


def _usable_input(page):
    """글자를 넣을 수 있는(보이고, 읽기 전용이 아닌) 검색 입력칸"""
    for css in SEARCH_INPUTS:
        inp = _first_visible(page, css)
        if inp is None:
            continue
        try:
            if inp.is_editable(timeout=1000):
                return inp
        except PWError:
            pass
    return None


def _find_search_input(page, sleep, log):
    inp = _usable_input(page)
    if inp is not None:
        return inp
    for sel in SEARCH_TRIGGERS:
        trigger = _first_visible(page, sel)
        if trigger is None:
            continue
        log(f"[검색] 검색 상자를 엽니다 ({sel})")
        try:
            # 개발자 도구 기기 모드에서는 마우스 클릭이 멈출 수 있어 페이지 안에서 직접 클릭
            trigger.evaluate("e => e.click()")
        except PWError:
            continue
        for _ in range(10):  # 입력칸이 나타날 때까지 최대 5초
            sleep(0.5, page)
            inp = _usable_input(page)
            if inp is not None:
                return inp
    # 상자를 못 찾으면 큐레이터 전용 검색 화면으로 직접 이동 (실제 주소: /search?inboundTag=curator)
    try:
        origin = "/".join(page.url.split("/")[:3])
        log(f"[검색] 큐레이터 검색 화면으로 직접 이동합니다: {origin}{CURATOR_SEARCH_PATH}")
        page.goto(origin + CURATOR_SEARCH_PATH, wait_until="domcontentloaded", timeout=15000)
        for _ in range(10):
            sleep(0.5, page)
            inp = _usable_input(page)
            if inp is not None:
                return inp
    except PWError:
        pass
    return None


def _run_search(page, keyword, sleep, log) -> bool:
    inp = _find_search_input(page, sleep, log)
    if inp is None:
        return False
    inp.evaluate("e => e.focus()")
    inp.fill(keyword, timeout=ACTION_TIMEOUT)      # input 이벤트까지 발생 → Angular 가 값을 인식
    inp.press("Enter", timeout=ACTION_TIMEOUT)
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except PWError:
        pass
    sleep(1.5, page)
    page.evaluate(HOOK_JS)  # 다른 페이지로 이동했으면 복사 가로채기를 다시 설치
    log(f"[검색] '{keyword}' 검색 완료 (화면: {page.url.split('?')[0]})")
    return True


def _back_to_list(page, list_url, sleep):
    """검색 전 큐레이터 목록으로 돌아가기"""
    try:
        if page.url != list_url:
            page.go_back(wait_until="domcontentloaded", timeout=10000)
            sleep(1.5, page)
        if page.url != list_url:
            page.goto(list_url, wait_until="domcontentloaded", timeout=15000)
            sleep(1.5, page)
        else:
            inp = _usable_input(page)  # 같은 화면에서 검색한 경우 검색어 지우기
            if inp is not None and inp.input_value(timeout=1000):
                inp.fill("", timeout=ACTION_TIMEOUT)
                inp.press("Enter", timeout=ACTION_TIMEOUT)
                sleep(1.5, page)
        page.evaluate("window.scrollTo(0, 0)")
        page.evaluate(HOOK_JS)
    except PWError:
        pass


def _collect_by_search(page, buttons, log, sleep, max_items, interval, stop_event, on_item, seen, dump,
                       state, keywords, per_search) -> bool:
    """시즌 키워드로 검색해서 수집. 검색창을 아예 찾지 못하면 False (→ 목록 훑기로 전환)"""
    list_url = page.url
    for i, kw in enumerate(keywords):
        if state["count"] >= max_items or _stopped(stop_event):
            break
        log(f"[검색] ({i + 1}/{len(keywords)}) '{kw}' 검색어 입력")
        try:
            ok = _run_search(page, kw, sleep, log)
        except PWError as e:
            log(f"[검색] '{kw}' 검색 중 오류: {str(e).splitlines()[0]}", "warn")
            ok = False
        if not ok:
            if i == 0:
                path = dump(page, "no_search_box")
                log(f"큐레이터 검색창을 사용하지 못했습니다 (화면 기록: {path}). 목록 훑기 방식으로 바꿉니다. "
                    "이 기록을 보내주시면 맞춰 고치겠습니다.", "warn")
                _back_to_list(page, list_url, sleep)
                return False
            _back_to_list(page, list_url, sleep)
            continue
        quota = min(per_search, max_items - state["count"])
        before = state["count"]
        if buttons.count() == 0:
            log(f"[검색] '{kw}': 검색 결과가 없습니다.")
        else:
            log(f"[검색] '{kw}': 결과에서 최대 {quota}개 수집")
            _collect_pass(page, buttons, log, sleep, max_items, interval, stop_event, on_item, seen, dump,
                          state, quota=quota, label=kw)
            log(f"[검색] '{kw}': {state['count'] - before}개 수집")
        _back_to_list(page, list_url, sleep)
    return True


def _collect_pass(page, buttons, log, sleep, max_items, interval, stop_event, on_item, seen, dump, state,
                  prefer=None, quota=None, label=None):
    """한 번 훑으며 수집.
    prefer: 시즌과 맞는 상품만 수집 (목록 훑기 1차) / quota: 이번에 수집할 최대 개수 (검색 결과용)
    label : 수집한 상품에 붙일 시즌 키워드 (검색어)"""
    idx, no_growth, got = 0, 0, 0
    while state["count"] < max_items and (quota is None or got < quota):
        if stop_event is not None and stop_event.is_set():
            log("사용자에 의해 수집을 중지했습니다.", "warn")
            return
        total = buttons.count()
        if idx >= total:
            # 아래로 스크롤해서 상품 더 불러오기
            page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
            sleep(1.5, page)
            if buttons.count() <= total:
                no_growth += 1
                if no_growth >= 3:
                    log("목록 끝까지 확인했습니다." if prefer else "더 이상 새 상품이 없습니다.")
                    return
            else:
                no_growth = 0
            continue

        btn = buttons.nth(idx)
        idx += 1
        try:
            # 클릭 전에 상품 정보를 먼저 읽어 이미 수집한 상품이면 [링크 생성]을 누르지 않음
            btn.scroll_into_view_if_needed(timeout=5000)
            sleep(0.3, page)
            info = parse_card(btn.evaluate(CARD_JS), page.url)
            key = info["product_id"] or info["name"]
            if key and key in seen:
                if prefer is None:
                    log(f"이미 수집한 상품이라 건너뜀: {info['name'][:30]}")
                continue
            matched = prefer(info) if prefer is not None else label
            if prefer is not None and not matched:
                continue  # 1차에서는 시즌 상품만
            item = _collect_one(page, btn, info, log, sleep)
            item["season_keyword"] = matched or ""
        except PWError as e:
            log(f"{idx}번째 상품 처리 중 오류: {str(e).splitlines()[0]}", "warn")
            continue
        if key:
            seen.add(key)

        stop_after = False
        if item["link"]:
            state["fails"] = 0
        else:
            state["fails"] += 1
            if state["fails"] in (1, 3):
                path = dump(page, f"link_not_found_{idx}")
                log(f"링크를 찾지 못했습니다 (화면 기록: {path}). 이 파일을 보내주시면 맞춰 고치겠습니다.", "warn")
            if state["fails"] >= 3:
                log("연속 3개 상품에서 링크를 찾지 못해 수집을 멈춥니다.", "error")
                stop_after = True

        state["count"] += 1
        got += 1
        on_item(item)
        tag = f" [시즌: {matched}]" if matched else ""
        log(f"[{state['count']}]{tag} {item['brand']} {item['name'][:30]} → "
            f"{item['link'] or '링크 없음'} ({item['link_source'] or '-'})",
            "success" if item["link"] else "warn")
        if stop_after:
            raise _TooManyFails()
        sleep(interval, page)


def _collect_one(page, btn, info, log, sleep):
    before = page.evaluate("() => window.__ekCopied.length")
    page.evaluate(MARK_BASE_JS)
    url_before = page.url
    responses = []

    def on_response(r):
        if r.request.resource_type in ("xhr", "fetch"):
            responses.append(r)

    page.on("response", on_response)
    clip_before = devtools_keys.get_clipboard_text()
    link = source = copied_text = ""
    dialog_urls, dialog_texts = [], []
    copy_clicked = mouse_retry = False
    try:
        # 개발자 도구 기기 모드에서는 마우스 좌표가 어긋나 Playwright 클릭이 멈출 수 있음
        # → 페이지 안에서 버튼을 직접 클릭 (사이트가 복사하는 링크는 가로채기로 받음)
        btn.evaluate("b => b.click()")
        start = time.time()
        deadline = start + 6
        while time.time() < deadline:
            if not mouse_retry and time.time() - start > 3 and not copied_text and not dialog_urls:
                mouse_retry = True  # 반응이 없으면 마우스 클릭으로 한 번 더
                try:
                    btn.click(timeout=2500, force=True, no_wait_after=True)
                except PWError:
                    pass
            copied = page.evaluate("(i) => window.__ekCopied.slice(i)", before)
            if copied:
                copied_text = copied[-1]
                m = URL_RE.search(copied_text)
                if m:
                    link, source = m.group(0), "클립보드"
                    break
            d = page.evaluate(DIALOG_JS)
            dialog_urls, dialog_texts = d["urls"] or dialog_urls, d["texts"] or dialog_texts
            if d["hasCopy"] and not copy_clicked:
                copy_clicked = True
                page.locator("[data-ek-copy]").first.evaluate("b => b.click()")
                continue
            if dialog_urls and (copy_clicked or not d["hasCopy"]) and time.time() > deadline - 4:
                break
            if page.url != url_before:  # 다른 페이지로 이동한 경우
                break
            clip = devtools_keys.get_clipboard_text()
            if clip and clip != clip_before and URL_RE.search(clip):
                copied_text = clip
                link, source = URL_RE.search(clip).group(0), "클립보드(Windows)"
                break
            sleep(0.3, page)
    finally:
        page.remove_listener("response", on_response)

    if not link and dialog_urls:
        link, source = dialog_urls[0], "팝업"

    if not link and page.url != url_before:
        log(f"[링크 생성] 후 다른 페이지로 이동했습니다: {page.url.split('?')[0]}", "warn")
        found = page.evaluate("() => [...document.querySelectorAll('input,textarea')].map(i => i.value)"
                              ".filter(v => /^https?:\\/\\//.test(v))")
        if found:
            link, source = found[0], "이동한 페이지"
        page.go_back(wait_until="domcontentloaded")
        sleep(1.5, page)
        page.evaluate(HOOK_JS)

    if not link:
        for r in responses:
            try:
                body = r.text()[:200000]
            except PWError:
                continue
            for u in URL_RE.findall(body):
                u = u.rstrip('\\",}')
                if not NOISE_URL.search(u):
                    link, source = u, "서버 응답(확인 필요)"
                    break
            if link:
                break

    if dialog_texts:
        log(f"  팝업/안내: {dialog_texts[0][:60]}")
    _close_layer(page, sleep)

    info.update({
        "link": link, "link_source": source, "copied_text": copied_text,
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    return info


def _close_layer(page, sleep):
    try:
        d = page.evaluate(DIALOG_JS)
        if d["hasClose"]:
            page.locator("[data-ek-close]").first.evaluate("b => b.click()")
            sleep(0.5, page)
        elif d["urls"] or d["hasCopy"]:
            page.keyboard.press("Escape")
            sleep(0.5, page)
    except PWError:
        pass
