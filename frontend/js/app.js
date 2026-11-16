"use strict";

const $ = (sel) => document.querySelector(sel);

const STATUS_LABEL = { ok: "완료", skipped: "건너뜀", failed: "실패" };
const SOURCE_LABEL = {
  google_trends: "구글 트렌드",
  season_keywords: "시즌",
  kakao_products: "카카오 선물하기",
};
const BUILT_STEPS = 3; // 화면이 만들어진 단계 수 (그 뒤는 '준비 중')
const STEP_TITLE = {
  4: "4. 추천 문구 생성",
  5: "5. 검수 · 복사 · 발행",
  6: "6. 성과 기록",
};

/* ---------- 공통 ---------- */

async function api(path, options = {}) {
  const res = await fetch(path, options);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}

function el(tag, attrs = {}, text) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined && text !== null) node.textContent = text; // textContent 로 XSS 방지
  return node;
}

function showError(id, msg) {
  const box = $(id);
  box.textContent = msg || "";
  box.hidden = !msg;
}

const won = (n) => (n || n === 0 ? `${Number(n).toLocaleString("ko-KR")}원` : "");
const fmtTime = (iso) => (iso || "").replace("T", " ");

async function runButton(btn, busyText, fn) {
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = busyText;
  try {
    await fn();
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

/* ---------- 단계 탭 ---------- */

function showStep(step) {
  const special = step === "settings" || step === "kakao" ? step : null;
  const n = special ? 0 : Number(step) || 1;
  document.querySelectorAll(".step").forEach((b) => b.classList.toggle("active", Number(b.dataset.step) === n));
  $("#btn-settings").classList.toggle("active", special === "settings");
  $("#btn-kakao").classList.toggle("active", special === "kakao");

  const target = special || (n <= BUILT_STEPS ? String(n) : "soon");
  document.querySelectorAll(".view").forEach((v) => { v.hidden = v.dataset.view !== target; });
  if (n > BUILT_STEPS) $("#soon-title").textContent = STEP_TITLE[n];
  if (n === 3) loadLinks();
  if (special === "settings") loadSettings();
  if (special === "kakao") enterKakao(); else leaveKakao();

  const hash = special ? `#${special}` : `#step-${n}`;
  if (location.hash !== hash) history.replaceState(null, "", hash);
}

/* =========================================================
   1단계: 트렌드 수집
   ========================================================= */

function renderSources(sources) {
  const grid = $("#source-status");
  grid.replaceChildren();
  const tpl = $("#tpl-source-card");
  for (const s of sources) {
    const card = tpl.content.cloneNode(true);
    card.querySelector(".source-name").textContent = s.label;
    const badge = card.querySelector(".badge");

    let cls, text;
    if (s.status) {
      cls = s.status;
      text = (STATUS_LABEL[s.status] || s.status) + (s.status === "ok" ? ` ${s.count}건` : "");
    } else {
      cls = s.ready ? "ok" : "skipped";
      text = s.ready ? "준비됨" : "사용 안 함";
    }
    badge.classList.add(cls);
    badge.textContent = text;
    card.querySelector(".source-msg").textContent = s.message || "";
    grid.appendChild(card);
  }
}

function metricFor(t) {
  const x = t.extra || {};
  if (t.source === "google_trends") return x.traffic ? `검색 ${x.traffic}` : "";
  if (t.source === "season_keywords") return x.season || "";
  if (t.source === "kakao_products") return won(x.benefit || x.price);
  return t.score ?? "";
}

function renderTrends(run) {
  const wrap = $("#results");
  wrap.replaceChildren();
  if (!run || !run.run_id) {
    wrap.appendChild(el("p", { class: "empty" }, "아직 수집 결과가 없습니다. ‘지금 수집’을 눌러 시작하세요."));
    $("#run-info").textContent = "";
    return;
  }
  $("#run-info").textContent = `마지막 수집: ${fmtTime(run.started_at)} · 총 ${run.trends.length}건`;

  for (const s of run.sources) {
    const col = el("article", { class: "result-col" });
    col.appendChild(el("h3", {}, s.label));
    const items = run.trends.filter((t) => t.source === s.name);

    if (!items.length) {
      col.appendChild(el("p", { class: "empty" }, s.status === "ok" ? "결과 없음" : s.message));
      wrap.appendChild(col);
      continue;
    }
    const ol = el("ol");
    for (const t of items) {
      const li = el("li");
      li.appendChild(el("span", { class: "rank" }, t.rank));
      li.appendChild(el("span", { class: "kw" }, t.keyword));
      li.appendChild(el("span", { class: "metric" }, metricFor(t)));
      const news = (t.extra && t.extra.news) || [];
      if (news.length && news[0].url) {
        const div = el("div", { class: "news" });
        div.appendChild(el("a", { href: news[0].url, target: "_blank", rel: "noopener noreferrer" }, news[0].title));
        li.appendChild(div);
      } else if (t.source === "kakao_products") {
        const x = t.extra || {};
        const sub = [x.brand, x.badge, x.discount && `${x.discount} 할인`].filter(Boolean).join(" · ");
        if (sub) li.appendChild(el("div", { class: "news muted" }, sub));
      }
      ol.appendChild(li);
    }
    col.appendChild(ol);
    wrap.appendChild(col);
  }
}

async function collect() {
  showError("#error-1", "");
  try {
    const run = await api("/api/collect", { method: "POST" });
    renderSources(run.sources);
    renderTrends(run);
  } catch (e) {
    showError("#error-1", `수집 실패: ${e.message}`);
  }
}

/* =========================================================
   2단계: 필터 · 상품 매칭
   ========================================================= */

function renderEngine(llm) {
  const badge = $("#engine-badge");
  badge.className = "badge " + (llm.provider === "gemini" ? "ok" : "info");
  badge.textContent = llm.provider === "gemini" ? `Gemini · ${llm.model}` : "규칙 기반";
  $("#engine-note").textContent =
    llm.provider === "gemini"
      ? "구글 트렌드 키워드를 Gemini가 판별합니다."
      : "⚙ 시스템 설정에서 Gemini API 키를 입력하면 Gemini 로 판별합니다.";
}

function stat(label, value) {
  const d = el("div", { class: "stat" });
  d.appendChild(el("div", { class: "label" }, label));
  d.appendChild(el("div", { class: "value" }, value));
  return d;
}

function productItem(p) {
  const li = el("li");
  if (!p.url) {
    li.appendChild(el("span", { class: "pname" }, p.name));
  } else {
    li.appendChild(el("a", { class: "pname", href: p.url, target: "_blank", rel: "noopener noreferrer" }, p.name));
  }
  li.appendChild(el("span", { class: "price" }, won(p.price)));
  const tags = el("div", { class: "ptags" });
  if (p.platform === "kakao") tags.appendChild(el("span", { class: "badge ok" }, "카카오 선물하기"));
  if (p.is_rocket) tags.appendChild(el("span", { class: "badge info" }, "로켓배송"));
  if (p.query) tags.appendChild(el("span", { class: "muted small" }, `검색어: ${p.query}`));
  li.appendChild(tags);
  return li;
}

function keywordCard(k) {
  const card = el("article", { class: "kw-card" });

  const head = el("div", { class: "kw-head" });
  head.appendChild(el("h3", {}, k.keyword));
  head.appendChild(el("span", { class: "badge" }, SOURCE_LABEL[k.source] || k.source));
  head.appendChild(el("span", { class: "score", title: "상품성 점수" }, k.score));
  card.appendChild(head);

  const bar = el("div", { class: "scorebar" });
  const fill = el("span");
  fill.style.width = `${Math.max(0, Math.min(100, k.score))}%`;
  bar.appendChild(fill);
  card.appendChild(bar);

  card.appendChild(el("p", { class: "reason" }, k.reason));

  const chips = el("div", { class: "chips" });
  if (k.category) chips.appendChild(el("span", { class: "chip cat" }, k.category));
  for (const q of k.search_queries || []) chips.appendChild(el("span", { class: "chip" }, q));
  if (chips.childElementCount) card.appendChild(chips);

  if (k.products.length) {
    const ul = el("ul", { class: "products" });
    k.products.forEach((p) => ul.appendChild(productItem(p)));
    card.appendChild(ul);
  } else {
    card.appendChild(el("p", { class: "empty small" },
      "맞는 카카오 상품이 없습니다. 🎁 카카오 상품에서 관련 상품에 이 키워드를 태그로 넣어 보세요."));
  }
  return card;
}

function renderAnalysis(a) {
  const grid = $("#keyword-grid");
  const stats = $("#filter-stats");
  const warns = $("#filter-warnings");
  grid.replaceChildren();
  stats.replaceChildren();
  warns.replaceChildren();
  $("#excluded-body").replaceChildren();

  if (!a || !a.id) {
    $("#filter-info").textContent = "";
    $("#excluded").hidden = true;
    grid.appendChild(el("p", { class: "empty" }, "아직 분석 결과가 없습니다. ‘필터·매칭 실행’을 눌러 시작하세요."));
    return;
  }

  const s = a.summary || {};
  stats.append(
    stat("전체 키워드", s.total ?? 0),
    stat(`통과 (${s.min_score ?? ""}점 이상)`, s.passed ?? 0),
    stat("제외", s.excluded ?? 0),
    stat("상품 붙은 키워드", s.matched_keywords ?? 0),
    stat("상품 후보 (키워드당 최대 " + (s.per_keyword ?? 3) + "개)", s.products ?? 0),
  );
  const engine = a.engine === "rule" ? "규칙 기반" : a.engine.replace("gemini:", "Gemini · ");
  const source = s.kakao_products !== undefined
    ? `카카오 상품 ${s.kakao_products}개에서 매칭 (상품 붙은 키워드 ${s.matched_keywords ?? 0}개)` : "";
  $("#filter-info").textContent =
    `분석 시각: ${fmtTime(a.analyzed_at)} · 수집 회차 ${a.run_id} · 판별 ${engine}` + (source ? ` · ${source}` : "");
  for (const w of s.warnings || []) warns.appendChild(el("li", {}, w));

  const passed = a.keywords.filter((k) => k.commercial && k.score >= (s.min_score ?? 0));
  const excluded = a.keywords.filter((k) => !(k.commercial && k.score >= (s.min_score ?? 0)));

  if (!passed.length) grid.appendChild(el("p", { class: "empty" }, "통과한 키워드가 없습니다."));
  passed.forEach((k) => grid.appendChild(keywordCard(k)));

  $("#excluded").hidden = !excluded.length;
  $("#excluded-count").textContent = `(${excluded.length})`;
  const tbody = $("#excluded-body");
  for (const k of excluded) {
    const tr = el("tr");
    tr.appendChild(el("td", {}, k.keyword));
    tr.appendChild(el("td", {}, SOURCE_LABEL[k.source] || k.source));
    tr.appendChild(el("td", { class: "num" }, k.score));
    tr.appendChild(el("td", {}, k.reason));
    tbody.appendChild(tr);
  }
}

async function runFilter() {
  showError("#error-2", "");
  try {
    renderAnalysis(await api("/api/filter", { method: "POST" }));
  } catch (e) {
    showError("#error-2", `필터·매칭 실패: ${e.message}`);
  }
}

/* =========================================================
   3단계: 제휴 링크 확정
   ========================================================= */

let linksData = null;

async function loadLinks() {
  showError("#error-3", "");
  try {
    linksData = await api("/api/links/latest");
    renderLinks();
  } catch (e) {
    showError("#error-3", `링크 후보를 불러오지 못했습니다: ${e.message}`);
  }
}

async function buildLinks() {
  showError("#error-3", "");
  if (linksData && linksData.links.some((l) => l.selected) &&
      !confirm("후보를 다시 만들면 지금 확정한 내용이 초기화됩니다. 계속할까요?")) return;
  try {
    linksData = await postJSON("/api/links/build");
    renderLinks();
  } catch (e) {
    showError("#error-3", `후보 만들기 실패: ${e.message}`);
  }
}

function renderLinks() {
  const d = linksData || { links: [] };
  const body = $("#links-body");
  const stats = $("#links-stats");
  body.replaceChildren();
  stats.replaceChildren();

  $("#links-stale").hidden = !(d.analysis_id && d.latest_analysis_id && d.latest_analysis_id !== d.analysis_id);
  if (!d.analysis_id) {
    $("#links-info").textContent = "";
    const tr = el("tr");
    tr.appendChild(el("td", { colspan: "6", class: "empty" },
      d.latest_analysis_id ? "[후보 만들기]를 눌러 2단계 결과로 링크 후보를 만드세요." : "먼저 2단계에서 '필터·매칭 실행'을 해 주세요."));
    body.appendChild(tr);
    return;
  }

  const selected = d.links.filter((l) => l.selected).length;
  stats.append(stat("링크 후보", d.links.length), stat("확정", selected),
    stat("미확정", d.links.length - selected));
  $("#links-info").textContent = `후보 만든 시각: ${fmtTime(d.built_at)} · 2단계 분석 #${d.analysis_id}`
    + " · 키워드 하나에 상품 하나, 같은 상품은 점수 높은 키워드에만 배정";
  renderSkipped(d.skipped || []);

  const onlySel = $("#links-only-selected").checked;
  const list = onlySel ? d.links.filter((l) => l.selected) : d.links;
  if (!list.length) {
    const tr = el("tr");
    tr.appendChild(el("td", { colspan: "6", class: "empty" }, onlySel ? "확정한 링크가 없습니다." : "링크 후보가 없습니다."));
    body.appendChild(tr);
  }
  for (const l of list) body.appendChild(linkRow(l));
}

function renderSkipped(skipped) {
  const box = $("#links-skipped");
  box.hidden = !skipped.length;
  $("#links-skipped-count").textContent = skipped.length;
  const ul = $("#links-skipped-list");
  ul.replaceChildren();
  for (const s of skipped) {
    ul.appendChild(el("li", {},
      `${s.keyword} (${s.score}점) — 후보 상품 '${(s.product || "").slice(0, 30)}'이(가) 이미 '${(s.taken_by || "").slice(0, 30)}' 키워드에 배정됨`));
  }
}

function linkRow(l) {
  const tr = el("tr", { class: l.selected ? "selected" : "" });

  // 확정 체크
  const chkTd = el("td");
  const chk = el("input", { type: "checkbox", title: "홍보 대상으로 확정" });
  chk.checked = l.selected;
  chk.disabled = !l.link_ok;
  chk.addEventListener("change", () => saveLink(l, { selected: chk.checked }, chk));
  chkTd.appendChild(chk);
  tr.appendChild(chkTd);

  // 키워드
  const kw = el("td", { class: "kwcell" });
  kw.appendChild(el("div", { class: "kwname" }, l.keyword));
  const meta = el("div", { class: "kwmeta" });
  meta.appendChild(el("span", { class: "badge" }, SOURCE_LABEL[l.source] || l.source));
  meta.appendChild(el("span", { class: "score", title: "상품성 점수" }, l.score));
  kw.appendChild(meta);
  tr.appendChild(kw);

  // 사진
  const imgTd = el("td");
  if (l.image) imgTd.appendChild(el("img", { src: l.image, alt: "", loading: "lazy", referrerpolicy: "no-referrer" }));
  tr.appendChild(imgTd);

  // 상품 (후보가 여러 개면 고를 수 있음)
  const prod = el("td", { class: "prod" });
  if (l.options.length > 1) {
    const sel = el("select", { "aria-label": "홍보할 상품 선택" });
    for (const o of l.options) {
      const opt = el("option", { value: o.product_id }, `${o.name}${o.price ? ` (${won(o.price)})` : ""}`);
      if (o.product_id === l.product_id) opt.selected = true;
      sel.appendChild(opt);
    }
    sel.addEventListener("change", () => saveLink(l, { product_id: sel.value }, sel));
    prod.appendChild(sel);
  } else {
    prod.appendChild(el("div", { class: "pname" }, l.product_name));
  }
  if (l.product_url) {
    prod.appendChild(el("a", { href: l.product_url, target: "_blank", rel: "noopener noreferrer" }, "상품 페이지 ↗"));
  }
  tr.appendChild(prod);

  tr.appendChild(el("td", { class: "price" }, won(l.price)));

  // 링크: 주소를 그대로 보여줌 (확정한 링크는 4단계 문구에 자동으로 들어감)
  const linkTd = el("td", { class: "linkcell" });
  const url = el("input", { type: "text", class: "url-field", readonly: "", value: l.link || "",
    title: "클릭하면 전체 선택", "aria-label": "제휴 링크 주소" });
  url.addEventListener("focus", () => url.select());
  linkTd.appendChild(url);
  if (!l.link_ok) linkTd.appendChild(el("div", { class: "badge failed" }, "링크 형식 오류"));
  tr.appendChild(linkTd);
  return tr;
}

async function saveLink(l, body, control) {
  showError("#error-3", "");
  control.disabled = true;
  try {
    const updated = await postJSON(`/api/links/${l.id}`, body);
    Object.assign(l, updated);
    renderLinks();
  } catch (e) {
    showError("#error-3", `저장 실패: ${e.message}`);
    renderLinks();
  }
}

/* =========================================================
   시스템 설정 — 핵심 3개(카카오 계정 · Gemini 키 · 개수) + 접어둔 고급 설정
   ========================================================= */

async function postJSON(path, body) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}

async function refreshEngine() {
  try {
    renderEngine((await api("/api/status")).llm);
  } catch { /* 설정 화면에서는 무시 */ }
}

let geminiModels = []; // 연결 테스트로 받은 Gemini 모델 목록

// 고급 설정으로 보낼 카카오 실행 옵션 (아이디·비번·수집 개수·시즌 우선은 기본 화면에)
const KAKAO_ADVANCED = [
  ["season_method", "select", "시즌 상품 찾는 방법",
    [["search", "큐레이터 검색창에 시즌 키워드 입력"], ["scan", "목록을 훑으며 상품명 비교"]]],
  ["search_per_keyword", "number", "검색할 때 키워드당 최대 개수", 1, 50, 1],
  ["collect_interval", "number", "상품 사이 대기(초)", 0.5, 30, 0.5],
  ["login_timeout", "number", "로그인 대기(초) — 카톡 승인·보안문자", 30, 600, 10],
  ["mobile_method", "select", "모바일 전환 방식",
    [["devtools", "개발자 도구 키 입력 (권장)"], ["emulation", "코드로 전환"]]],
  ["mobile_device", "device", "기기 (코드로 전환일 때)"],
  ["open_curator", "check", "로그인 후 [큐레이터] 열기"],
  ["mobile_mode", "check", "모바일 화면으로 전환"],
  ["close_devtools", "check", "전환 후 개발자 도구 닫기"],
  ["auto_collect", "check", "실행할 때 링크 수집까지 진행"],
  ["headless", "check", "브라우저 창 숨김 (권장하지 않음)"],
];

async function loadSettings() {
  showError("#error-settings", "");
  try {
    const [env, kakao, app] = await Promise.all([
      api("/api/settings"), api("/api/kakao/settings"), api("/api/app-settings"),
    ]);
    renderSettings(env, kakao, app);
  } catch (e) {
    showError("#error-settings", `설정을 불러오지 못했습니다: ${e.message}`);
  }
}

/* ---------- 작은 부품 ---------- */

function setCard(title, status) {
  const card = el("section", { class: "panel set-card" });
  const head = el("div", { class: "set-head" });
  head.appendChild(el("h3", {}, title));
  if (status) head.appendChild(el("span", { class: `badge ${status[0]}` }, status[1]));
  card.appendChild(head);
  return card;
}

function setRow(label, control, note) {
  const row = el("label", { class: "set-row" });
  row.appendChild(el("span", { class: "set-label" }, label));
  const right = el("span", { class: "set-control" });
  right.appendChild(control);
  if (note) right.appendChild(el("span", { class: "set-note" }, note));
  row.appendChild(right);
  return row;
}

function textInput(type, value, placeholder) {
  const i = el("input", { type, autocomplete: type === "password" ? "new-password" : "off", spellcheck: "false" });
  if (value !== undefined && value !== null) i.value = value;
  if (placeholder) i.placeholder = placeholder;
  return i;
}

function numberInput(value, min, max, step = 1) {
  const i = el("input", { type: "number", min, max, step, class: "num" });
  i.value = value;
  return i;
}

function passwordBox(placeholder) {
  const wrap = el("span", { class: "pw-box" });
  const input = textInput("password", "", placeholder);
  const eye = el("button", { type: "button", class: "btn small" }, "보기");
  eye.addEventListener("click", (e) => {
    e.preventDefault();
    input.type = input.type === "password" ? "text" : "password";
    eye.textContent = input.type === "password" ? "보기" : "숨기기";
  });
  wrap.append(input, eye);
  return { wrap, input };
}

function checkbox(checked) {
  const c = el("input", { type: "checkbox" });
  c.checked = !!checked;
  return c;
}

function actionsRow(...buttons) {
  const row = el("div", { class: "card-actions" });
  row.append(...buttons);
  return row;
}

function button(text, cls, onClick, busy = "처리 중…") {
  const b = el("button", { type: "button", class: `btn ${cls || ""}` }, text);
  b.addEventListener("click", (e) => runButton(e.currentTarget, busy, onClick));
  return b;
}

function resultLine() {
  return el("p", { class: "test-result", hidden: "" });
}

function showResult(card, ok, msg) {
  const p = card.querySelector(".test-result");
  p.className = `test-result ${ok ? "ok" : "fail"}`;
  p.textContent = msg;
  p.hidden = false;
}

/* ---------- 화면 ---------- */

function renderSettings(env, kakao, app) {
  const box = $("#settings-groups");
  box.replaceChildren(
    kakaoCard(kakao),
    geminiCard(env),
    countsCard(app),
    advancedCard(env, kakao, app),
  );
}

// 1) 카카오 선물하기 계정
function kakaoCard(s) {
  const ok = s.user_id && s.has_password;
  const card = setCard("카카오 선물하기 계정",
    ok ? ["ok", "연결됨"] : s.user_id ? ["mock", "비밀번호 없음"] : ["skipped", "미설정"]);

  const id = textInput("text", s.user_id, "example@kakao.com");
  const pw = passwordBox(s.has_password ? "저장됨 · 바꿀 때만 입력" : "비밀번호");
  const max = numberInput(s.collect_max, 1, 500);
  const season = checkbox(s.season_first);

  card.append(
    setRow("아이디", id),
    setRow("비밀번호", pw.wrap, "Windows 자격 증명 관리자에 저장"),
    setRow("한 번에 수집할 개수", max),
    setRow("시즌 상품 먼저", season, "시즌 키워드로 검색해 먼저 모으고, 남는 개수는 나머지로"),
  );

  const save = button("저장", "primary", async () => {
    if (!id.value.trim()) return showResult(card, false, "아이디를 입력하세요.");
    if (!pw.input.value && !s.has_password) return showResult(card, false, "비밀번호를 입력하세요.");
    try {
      await postJSON("/api/kakao/settings", {
        user_id: id.value.trim(), password: pw.input.value,
        collect_max: Number(max.value), season_first: season.checked,
      });
      await loadSettings();
      showResult($("#settings-groups").children[0], true, "저장했습니다.");
    } catch (e) {
      showResult(card, false, `저장 실패: ${e.message}`);
    }
  }, "저장 중…");
  const buttons = [save];
  if (s.user_id) {
    buttons.push(button("로그인 정보 삭제", "link", async () => {
      if (!confirm("저장된 카카오 아이디와 비밀번호를 삭제할까요?")) return;
      await postJSON("/api/kakao/settings/delete");
      await loadSettings();
    }));
  }
  card.append(actionsRow(...buttons), resultLine());
  return card;
}

// 2) Gemini API 키
function geminiCard(env) {
  const f = env.fields.find((x) => x.name === "GEMINI_API_KEY") || {};
  const card = setCard("Gemini API 키 (무료)", f.is_set ? ["ok", "연결됨"] : ["skipped", "미설정 · 규칙 기반으로 동작"]);
  const key = passwordBox(f.is_set ? `저장됨 ${f.display} · 바꿀 때만 입력` : "aistudio.google.com 에서 발급한 키");
  card.append(setRow("API 키", key.wrap, "키워드 판별·문구 생성에 사용"));

  const save = button("저장", "primary", async () => {
    if (!key.input.value.trim()) return showResult(card, false, "키를 입력하세요.");
    try {
      await postJSON("/api/settings", { values: { GEMINI_API_KEY: key.input.value.trim() } });
      refreshEngine();
      await loadSettings();
      showResult($("#settings-groups").children[1], true, "저장했습니다.");
    } catch (e) {
      showResult(card, false, `저장 실패: ${e.message}`);
    }
  }, "저장 중…");
  const test = button("연결 테스트", "", async () => {
    try {
      const r = await postJSON("/api/settings/test/gemini");
      if (r.models) geminiModels = r.models;
      showResult(card, r.ok, r.message);
    } catch (e) {
      showResult(card, false, `테스트 실패: ${e.message}`);
    }
  }, "확인 중…");
  const buttons = [save, test];
  if (f.is_set) {
    buttons.push(button("키 삭제", "link", async () => {
      if (!confirm("저장된 Gemini API 키를 삭제할까요?")) return;
      await postJSON("/api/settings", { clear: ["GEMINI_API_KEY"] });
      refreshEngine();
      await loadSettings();
    }));
  }
  card.append(actionsRow(...buttons), resultLine());
  return card;
}

// 3) 개수 · 기준 (핵심만)
function countsCard(app) {
  const card = setCard("개수 · 기준");
  const inputs = appFieldRows(card, app.fields.filter((f) => f.core));
  card.append(actionsRow(button("저장", "primary", () => saveAppFields(card, inputs), "저장 중…")), resultLine());
  return card;
}

function appFieldRows(card, fields) {
  const inputs = {};
  for (const f of fields) {
    const input = numberInput(f.value, f.min, f.max);
    inputs[f.key] = input;
    card.appendChild(setRow(f.label, input, f.help || (f.core ? "" : f.step)));
  }
  return inputs;
}

async function saveAppFields(card, inputs) {
  const values = {};
  for (const [k, i] of Object.entries(inputs)) values[k] = Number(i.value);
  try {
    await postJSON("/api/app-settings", { values });
    showResult(card, true, "저장했습니다. 다음 실행부터 적용됩니다.");
  } catch (e) {
    showResult(card, false, `저장 실패: ${e.message}`);
  }
}

// 4) 고급 설정 (접어둠)
function advancedCard(env, kakao, app) {
  const det = el("details", { class: "panel set-card set-advanced" });
  det.appendChild(el("summary", {}, "고급 설정"));

  // 카카오 실행 옵션
  const k = el("div", { class: "set-sub" });
  k.appendChild(el("h4", {}, "카카오 자동 수집"));
  const kInputs = {};
  for (const [key, type, label, a, b, step] of KAKAO_ADVANCED) {
    let input;
    if (type === "check") {
      input = checkbox(kakao[key]);
    } else if (type === "number") {
      input = numberInput(kakao[key], a, b, step);
    } else {
      input = el("select");
      const choices = type === "device" ? kakao.mobile_devices.map((d) => [d, d]) : a;
      for (const [v, t] of choices) {
        const o = el("option", { value: v }, t);
        if (v === kakao[key]) o.selected = true;
        input.appendChild(o);
      }
    }
    kInputs[key] = input;
    k.appendChild(setRow(label, input));
  }
  k.append(actionsRow(
    button("저장", "primary", async () => {
      const body = {};
      for (const [key, i] of Object.entries(kInputs)) {
        body[key] = i.type === "checkbox" ? i.checked : i.type === "number" ? Number(i.value) : i.value;
      }
      try {
        await postJSON("/api/kakao/settings", body);
        showResult(k, true, "저장했습니다.");
      } catch (e) {
        showResult(k, false, `저장 실패: ${e.message}`);
      }
    }, "저장 중…"),
    button("로그인 상태 초기화", "", async () => {
      if (!confirm("자동화 브라우저에 저장된 카카오 로그인 상태를 지울까요? 다음 실행 때 다시 로그인합니다.")) return;
      try {
        await postJSON("/api/kakao/session/clear");
        showResult(k, true, "로그인 상태를 초기화했습니다.");
      } catch (e) {
        showResult(k, false, `초기화 실패: ${e.message}`);
      }
    }, "초기화 중…"),
  ), resultLine());

  // 나머지 개수·기준
  const c = el("div", { class: "set-sub" });
  c.appendChild(el("h4", {}, "그 밖의 개수 · 기준"));
  const cInputs = appFieldRows(c, app.fields.filter((f) => !f.core));
  c.append(actionsRow(button("저장", "primary", () => saveAppFields(c, cInputs), "저장 중…")), resultLine());

  // Gemini 모델
  const g = el("div", { class: "set-sub" });
  g.appendChild(el("h4", {}, "Gemini 모델"));
  const mf = env.fields.find((x) => x.name === "GEMINI_MODEL") || {};
  const model = textInput("text", mf.display || "", "비워두면 기본값 (gemini-flash-lite-latest)");
  model.setAttribute("list", "gemini-models");
  const dl = el("datalist", { id: "gemini-models" });
  for (const m of geminiModels) dl.appendChild(el("option", { value: m.name }, m.display_name));
  g.append(setRow("모델", model, "Gemini 카드의 [연결 테스트]를 누르면 목록에서 고를 수 있음"), dl);
  g.append(actionsRow(button("저장", "primary", async () => {
    try {
      const v = model.value.trim();
      await postJSON("/api/settings", v ? { values: { GEMINI_MODEL: v } } : { clear: ["GEMINI_MODEL"] });
      refreshEngine();
      showResult(g, true, v ? `모델을 ${v}(으)로 저장했습니다.` : "기본 모델을 사용합니다.");
    } catch (e) {
      showResult(g, false, `저장 실패: ${e.message}`);
    }
  }, "저장 중…")), resultLine());

  det.append(k, c, g);
  return det;
}

/* =========================================================
   카카오 선물하기 상품 화면
   ========================================================= */

let kkTimer = null;
let kkLastLog = 0;
let kkProducts = [];
let kkLastCollected = -1;
let kkWasBusy = false;

async function enterKakao() {
  if (kkTimer) return;
  showError("#error-kakao", "");
  await Promise.all([kakaoPoll(), loadKakaoProducts(), kakaoAccount()]);
  kkTimer = setInterval(kakaoPoll, 1000);
}

function leaveKakao() {
  if (kkTimer) clearInterval(kkTimer);
  kkTimer = null;
}

async function kakaoAccount() {
  try {
    const s = await api("/api/kakao/settings");
    $("#kk-account").textContent = s.user_id && s.has_password
      ? `계정: ${s.user_id} · 최대 ${s.collect_max}개 수집 · 상품 사이 ${s.collect_interval}초`
      : "⚙ 시스템 설정에서 카카오 아이디와 비밀번호를 먼저 저장하세요.";
  } catch { /* 무시 */ }
}

async function kakaoPoll() {
  try {
    const [st, logs] = await Promise.all([api("/api/kakao/status"), api(`/api/kakao/logs?since=${kkLastLog}`)]);
    $("#kk-step").textContent = st.step + (st.last_error ? ` — ${st.last_error}` : "");
    $("#kk-bar").style.width = `${st.busy ? st.progress : st.step.includes("완료") ? 100 : 0}%`;
    $("#kk-dot").className = "dot " + (st.busy ? "busy" : st.last_error ? "fail" : st.browser_open ? "ok" : "");
    $("#kk-run").disabled = st.busy;
    $("#kk-collect").disabled = st.busy || !st.browser_open;
    $("#kk-stop").disabled = !st.busy;
    $("#kk-close").disabled = st.busy || !st.browser_open;
    $("#kk-import").disabled = st.busy;

    const box = $("#kk-log");
    const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 10;
    for (const l of logs) {
      box.appendChild(el("div", { class: l.level }, `${l.time}  ${l.msg}`));
      kkLastLog = l.id;
    }
    if (logs.length && atBottom) box.scrollTop = box.scrollHeight;
    if (logs.some((l) => l.level === "warn" || l.level === "error")) box.parentElement.open = true;

    // 수집 중 새 상품이 들어오거나 작업이 끝나면 목록 갱신
    if (st.collected !== kkLastCollected || (kkWasBusy && !st.busy)) {
      kkLastCollected = st.collected;
      if (kkWasBusy || st.busy) loadKakaoProducts();
    }
    kkWasBusy = st.busy;
  } catch (e) {
    $("#kk-step").textContent = `서버 연결 실패: ${e.message}`;
  }
}

async function kakaoAction(path, okMsg) {
  showError("#error-kakao", "");
  try {
    await postJSON(path);
    if (okMsg) $("#kk-step").textContent = okMsg;
    $("#kk-log").parentElement.open = true;
    kakaoPoll();
  } catch (e) {
    showError("#error-kakao", e.message);
  }
}

async function loadKakaoProducts() {
  try {
    kkProducts = (await api("/api/kakao/products")).products;
    renderKakaoProducts();
  } catch (e) {
    showError("#error-kakao", `상품 목록을 불러오지 못했습니다: ${e.message}`);
  }
}

function renderKakaoProducts() {
  const q = $("#kk-filter").value.trim().toLowerCase();
  const list = kkProducts.filter((p) =>
    !q || `${p.brand} ${p.name} ${p.tags}`.toLowerCase().includes(q));
  $("#kk-count").textContent = q ? `${list.length} / ${kkProducts.length}` : kkProducts.length;
  const body = $("#kk-body");
  body.replaceChildren();
  if (!list.length) {
    const tr = el("tr");
    const td = el("td", { colspan: "6", class: "empty" },
      kkProducts.length ? "검색 결과가 없습니다." : "아직 상품이 없습니다. [실행] 또는 [예전 수집 결과 가져오기]를 눌러 주세요.");
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }
  for (const p of list) body.appendChild(kakaoRow(p));
}

function kakaoRow(p) {
  const tr = el("tr", { class: p.active ? "" : "inactive" });

  const imgTd = el("td");
  if (p.image) imgTd.appendChild(el("img", { src: p.image, alt: "", loading: "lazy", referrerpolicy: "no-referrer" }));
  tr.appendChild(imgTd);

  const info = el("td", { class: "pinfo" });
  if (p.brand) info.appendChild(el("div", { class: "brand" }, p.brand));
  if (p.product_url) {
    info.appendChild(el("a", { href: p.product_url, target: "_blank", rel: "noopener noreferrer" }, p.name));
  } else {
    info.appendChild(el("div", {}, p.name));
  }
  const meta = el("div", { class: "meta" });
  if (p.badge) meta.appendChild(el("span", { class: "badge info" }, p.badge));
  if (p.link) {
    const copy = el("button", { type: "button", class: "btn small" }, "링크 복사");
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(p.link);
        copy.textContent = "복사됨";
      } catch {
        prompt("아래 링크를 복사하세요", p.link);
      }
      setTimeout(() => (copy.textContent = "링크 복사"), 1500);
    });
    meta.appendChild(copy);
  } else {
    meta.appendChild(el("span", { class: "badge failed" }, "링크 없음"));
  }
  info.appendChild(meta);
  tr.appendChild(info);

  const price = el("td", { class: "price" });
  if (p.benefit && p.price && p.benefit !== p.price) {
    price.appendChild(el("s", {}, won(p.price)));
    price.appendChild(el("br"));
    price.appendChild(el("span", {}, `${won(p.benefit)}`));
  } else {
    price.appendChild(el("span", {}, won(p.benefit || p.price)));
  }
  if (p.discount) price.appendChild(el("div", { class: "muted small" }, `${p.discount} 할인`));
  tr.appendChild(price);

  const tagTd = el("td");
  const tags = el("input", { type: "text", value: p.tags || "", placeholder: "예: 수능, 수험생" });
  tags.addEventListener("change", async () => {
    try {
      await postJSON(`/api/kakao/products/${p.id}`, { tags: tags.value });
      p.tags = tags.value;
      tags.style.borderColor = "var(--accent)";
    } catch (e) {
      tags.style.borderColor = "var(--danger)";
      showError("#error-kakao", `태그 저장 실패: ${e.message}`);
    }
  });
  tagTd.appendChild(tags);
  tr.appendChild(tagTd);

  const actTd = el("td");
  const active = el("input", { type: "checkbox", title: "끄면 2단계 매칭에서 제외" });
  active.checked = p.active;
  active.addEventListener("change", async () => {
    try {
      await postJSON(`/api/kakao/products/${p.id}`, { active: active.checked });
      p.active = active.checked;
      tr.className = p.active ? "" : "inactive";
    } catch (e) {
      active.checked = !active.checked;
      showError("#error-kakao", `변경 실패: ${e.message}`);
    }
  });
  actTd.appendChild(active);
  tr.appendChild(actTd);

  const delTd = el("td");
  const del = el("button", { type: "button", class: "btn link" }, "삭제");
  del.addEventListener("click", async () => {
    if (!confirm(`'${p.name.slice(0, 30)}' 상품을 목록에서 삭제할까요?`)) return;
    try {
      await postJSON(`/api/kakao/products/${p.id}/delete`);
      kkProducts = kkProducts.filter((x) => x.id !== p.id);
      renderKakaoProducts();
    } catch (e) {
      showError("#error-kakao", `삭제 실패: ${e.message}`);
    }
  });
  delTd.appendChild(del);
  tr.appendChild(delTd);
  return tr;
}

async function kakaoImport(btn) {
  showError("#error-kakao", "");
  if (!confirm("예전 earning_kakao 프로그램의 수집 결과(results.json)를 가져올까요?\n이미 있는 상품은 최신 정보로 갱신됩니다.")) return;
  await runButton(btn, "가져오는 중…", async () => {
    try {
      const r = await postJSON("/api/kakao/import");
      $("#kk-step").textContent =
        `가져오기 완료: 새 상품 ${r.insert}개, 갱신 ${r.update}개, 상품명 복구 ${r.renamed}개, 건너뜀 ${r.skip}개`;
      await loadKakaoProducts();
    } catch (e) {
      showError("#error-kakao", `가져오기 실패: ${e.message}`);
    }
  });
}

/* =========================================================
   단계별 초기화
   ========================================================= */

const RESET_MESSAGE = {
  1: "1단계 수집 결과를 지웁니다.\n이 결과로 만든 2단계 분석과 3단계 링크 후보도 함께 지워집니다.\n(카카오 상품과 설정은 그대로)",
  2: "2단계 분석 결과를 지웁니다.\n이 결과로 만든 3단계 링크 후보도 함께 지워집니다.\n(1단계 수집 결과는 그대로)",
  3: "3단계 링크 후보와 확정 내용을 지웁니다.\n(1·2단계 결과는 그대로)",
};

async function resetStep(step, btn) {
  if (!confirm(RESET_MESSAGE[step] + "\n\n계속할까요?")) return;
  await runButton(btn, "지우는 중…", async () => {
    const errBox = `#error-${step}`;
    showError(errBox, "");
    try {
      await postJSON(`/api/reset/${step}`);
      // 지운 단계와 그 뒤 단계 화면을 다시 그림
      if (step <= 1) {
        renderSources((await api("/api/status")).collectors);
        renderTrends(null);
      }
      if (step <= 2) renderAnalysis(null);
      linksData = null;
      if (step <= 3) await loadLinks();
    } catch (e) {
      showError(errBox, `초기화 실패: ${e.message}`);
    }
  });
}

async function clearKakaoProducts(btn) {
  if (!confirm(`저장된 카카오 상품 ${kkProducts.length}개를 모두 지울까요?
태그도 함께 지워지고 되돌릴 수 없습니다.
(다시 [실행]하거나 [예전 수집 결과 가져오기]로 채울 수 있습니다)`)) return;
  await runButton(btn, "지우는 중…", async () => {
    try {
      await postJSON("/api/kakao/products/clear");
      await loadKakaoProducts();
    } catch (e) {
      showError("#error-kakao", `삭제 실패: ${e.message}`);
    }
  });
}

/* ---------- 시작 ---------- */

async function init() {
  document.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => showStep(b.dataset.step)));
  $("#btn-settings").addEventListener("click", () => showStep("settings"));
  $("#btn-kakao").addEventListener("click", () => showStep("kakao"));
  $("#btn-collect").addEventListener("click", (e) => runButton(e.currentTarget, "수집 중…", collect));
  $("#btn-filter").addEventListener("click", (e) => runButton(e.currentTarget, "분석 중…", runFilter));
  $("#btn-links-build").addEventListener("click", (e) => runButton(e.currentTarget, "만드는 중…", buildLinks));
  $("#links-only-selected").addEventListener("change", renderLinks);
  $("#kk-run").addEventListener("click", () => kakaoAction("/api/kakao/run", "실행 요청을 보냈습니다. 크롬 창을 확인하세요."));
  $("#kk-collect").addEventListener("click", () => kakaoAction("/api/kakao/collect", "링크 수집을 시작합니다."));
  $("#kk-stop").addEventListener("click", () => kakaoAction("/api/kakao/stop"));
  $("#kk-close").addEventListener("click", () => kakaoAction("/api/kakao/browser/close", "브라우저를 닫았습니다."));
  $("#kk-import").addEventListener("click", (e) => kakaoImport(e.currentTarget));
  $("#kk-filter").addEventListener("input", renderKakaoProducts);
  $("#kk-clear").addEventListener("click", (e) => clearKakaoProducts(e.currentTarget));
  document.querySelectorAll("[data-reset]").forEach((b) =>
    b.addEventListener("click", () => resetStep(Number(b.dataset.reset), b)));
  const hash = location.hash.replace("#", "");
  showStep(hash === "settings" || hash === "kakao" ? hash : (hash.match(/step-(\d)/) || [])[1] || 1);

  try {
    const [status, latest, analysis] = await Promise.all([
      api("/api/status"),
      api("/api/trends/latest"),
      api("/api/filter/latest"),
    ]);
    renderSources(latest.run_id ? latest.sources : status.collectors);
    renderTrends(latest);
    renderEngine(status.llm);
    renderAnalysis(analysis);
  } catch (e) {
    showError("#error-1", `서버 연결 실패: ${e.message}`);
  }
}

init();
