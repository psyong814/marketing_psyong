"use strict";

const $ = (sel) => document.querySelector(sel);

const STATUS_LABEL = { ok: "완료", skipped: "건너뜀", failed: "실패" };

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

function showError(msg) {
  const box = $("#error");
  box.textContent = msg || "";
  box.hidden = !msg;
}

/* ---------- 소스 상태 카드 ---------- */

function renderSources(sources) {
  const grid = $("#source-status");
  grid.replaceChildren();
  const tpl = $("#tpl-source-card");
  for (const s of sources) {
    const card = tpl.content.cloneNode(true);
    card.querySelector(".source-name").textContent = s.label;
    const badge = card.querySelector(".badge");

    let cls, text;
    if (s.status) {               // 수집 실행 결과
      cls = s.status;
      text = STATUS_LABEL[s.status] + (s.status === "ok" ? ` ${s.count}건` : "");
      if (s.status === "ok" && s.mock) { cls = "mock"; text += " · MOCK"; }
    } else {                      // 실행 전 준비 상태
      cls = !s.ready ? "skipped" : s.mock ? "mock" : "ok";
      text = !s.ready ? "키 필요" : s.mock ? "MOCK" : "준비됨";
    }
    badge.classList.add(cls);
    badge.textContent = text;
    card.querySelector(".source-msg").textContent = s.message || "";
    grid.appendChild(card);
  }
}

/* ---------- 결과 목록 ---------- */

function metricFor(t) {
  const x = t.extra || {};
  if (t.source === "google_trends") return { text: x.traffic ? `검색 ${x.traffic}` : "" };
  if (t.source === "naver_datalab") {
    const g = Number(x.growth_pct || 0);
    return { text: `${g >= 0 ? "+" : ""}${g.toFixed(1)}%`, cls: g >= 0 ? "up" : "down" };
  }
  if (t.source === "coupang_goldbox") {
    return { text: x.price ? `${Number(x.price).toLocaleString("ko-KR")}원` : "" };
  }
  return { text: t.score ?? "" };
}

function renderResults(run) {
  const wrap = $("#results");
  wrap.replaceChildren();
  if (!run || !run.run_id) {
    wrap.appendChild(el("p", { class: "empty" }, "아직 수집 결과가 없습니다. ‘지금 수집’을 눌러 시작하세요."));
    $("#run-info").textContent = "";
    return;
  }

  $("#run-info").textContent = `마지막 수집: ${run.started_at.replace("T", " ")} · 총 ${run.trends.length}건`;

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
      const m = metricFor(t);
      li.appendChild(el("span", { class: `metric ${m.cls || ""}` }, m.text));

      const news = (t.extra && t.extra.news) || [];
      if (news.length && news[0].url) {
        const div = el("div", { class: "news" });
        const a = el("a", { href: news[0].url, target: "_blank", rel: "noopener noreferrer" }, news[0].title);
        div.appendChild(a);
        li.appendChild(div);
      }
      ol.appendChild(li);
    }
    col.appendChild(ol);
    wrap.appendChild(col);
  }
}

/* ---------- 동작 ---------- */

async function collect() {
  const btn = $("#btn-collect");
  btn.disabled = true;
  btn.textContent = "수집 중…";
  showError("");
  try {
    const run = await api("/api/collect", { method: "POST" });
    renderSources(run.sources);
    renderResults(run);
  } catch (e) {
    showError(`수집 실패: ${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "지금 수집";
  }
}

async function init() {
  $("#btn-collect").addEventListener("click", collect);
  try {
    const [status, latest] = await Promise.all([api("/api/status"), api("/api/trends/latest")]);
    renderSources(latest.run_id ? latest.sources : status.collectors);
    renderResults(latest);
  } catch (e) {
    showError(`서버 연결 실패: ${e.message}`);
  }
}

init();
