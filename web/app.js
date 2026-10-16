(() => {
"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const NS = "http://www.w3.org/2000/svg";
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

const ADVISERS = {
  finance:  { name: "Finance",  glyph: "rupee" },
  business: { name: "Business", glyph: "bars" },
  customer: { name: "Customer", glyph: "person" },
  legal:    { name: "Legal",    glyph: "scales" },
  infosec:  { name: "InfoSec & Data Privacy", short: "InfoSec", glyph: "shield" },
  ops:      { name: "Ops & Engineering", short: "Ops", glyph: "gear" },
};
const SEAT_ORDER = ["finance", "business", "customer", "legal", "infosec", "ops"];
const STAGES = [
  { key: "routing",    label: "Routing" },
  { key: "advisers",   label: "Advisers debate" },
  { key: "moderating", label: "Claim checks" },
  { key: "scoring",    label: "Scorecard" },
  { key: "writing",    label: "Memo" },
  { key: "checking",   label: "Final check" },
  { key: "done",       label: "Ready for sign-off" },
];
const ROUTING = { floor: 0.55, margin: 0.06 };

let FACTS = {};
let SAMPLE = {};

const state = { mode: "sample", runs: [], runId: null, vm: null, poll: null, replaying: false, stageIdx: 6 };

/* ---------------- helpers ---------------- */
function advName(id, short) { const a = ADVISERS[id]; return a ? (short && a.short) || a.name : (id || "Adviser"); }
function last(a) { return Array.isArray(a) && a.length ? a[a.length - 1] : null; }
function stanceKey(s) {
  s = String(s || "").toLowerCase();
  if (s.includes("_b") || s === "b") return "b";
  if (s.includes("_a") || s === "a") return "a";
  if (s.includes("cond")) return "c";
  return "n";
}
function stanceLabel(k, opts) {
  if (k === "a") return "Backs option A";
  if (k === "b") return "Backs option B";
  if (k === "c") return "Conditional";
  return "Not enough evidence";
}
function shortOpt(t, fb) { if (!t) return fb; t = String(t); return t.length > 28 ? t.slice(0, 26).trim() + "…" : t; }
function fmtDur(s) { if (s == null || isNaN(s)) return ""; s = Math.round(s); if (s < 60) return s + " s"; return Math.floor(s / 60) + " min " + String(s % 60).padStart(2, "0") + " s"; }
function fmtClock(s) { s = Math.max(0, Math.round(s || 0)); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); }
function fmtNum(v, unit) {
  if (v == null || v === "") return "";
  const n = Number(v); const t = isNaN(n) ? v : n.toLocaleString("en-IN", { maximumFractionDigits: 2 });
  unit = unit || "";
  if (unit.startsWith("₹ ")) return "₹" + t + " " + unit.slice(2);
  if (unit === "%" ) return t + "%";
  return (t + " " + unit).trim();
}
function fmtTime(iso) { if (!iso) return ""; const d = new Date(iso); if (isNaN(d)) return String(iso); return d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); }
function upper(s) { return String(s || "").toUpperCase(); }

function chip(id) {
  const f = FACTS[id] || {};
  const cls = ["chip"]; if (f.superseded_by) cls.push("old"); else if (f.reliability === "low") cls.push("low");
  return `<button type="button" class="${cls.join(" ")}" data-ev="${esc(id)}" title="Open evidence ${esc(id)}">${esc(id)}</button>`;
}
// turn "(SEC-01, LEG-08; InfoSec)" style references in text into chips
const EVID_G = /\b(?:FIN|BUS|CUS|LEG|SEC|OPS)-\d{2,3}\b|\bBRIEF\b/g;
const EVID_T = /\b(?:FIN|BUS|CUS|LEG|SEC|OPS)-\d{2,3}\b|\bBRIEF\b/;
function withChips(text) {
  const raw = String(text || "");
  const ids = [...new Set(raw.match(EVID_G) || [])];
  let by = "";
  const clean = raw.replace(/\s*\(([^)]*)\)\s*\.?\s*$/, (m, inner) => {
    if (!EVID_T.test(inner)) return m;
    by = inner.replace(EVID_G, "").replace(/^[\s,;]+|[\s,;]+$/g, "").replace(/\s*[;,]\s*[;,]\s*/g, ", ");
    return m.trim().endsWith(".") ? "." : "";
  });
  return { text: clean, ids, by };
}
function byLine(w) { return w.by ? `<span class="by">Raised by ${esc(w.by)}</span>` : ""; }

/* ---------------- adapter: API run -> view model ---------------- */
function toViewModel(run) {
  run = run || {};
  const brief = run.brief || {};
  const routing = run.routing || {};
  let positions = run.positions || [];
  if (!Array.isArray(positions)) positions = Object.entries(positions).map(([k, v]) => ({ adviser_id: k, ...v }));
  const verdictList = run.claim_verdicts || run.verdicts || [];
  const verdicts = {};
  (Array.isArray(verdictList) ? verdictList : Object.values(verdictList)).forEach(v => { if (v && v.claim_id) verdicts[v.claim_id] = v; });
  const memoRevs = (run.memo_revisions || run.memo_versions || []).map(m => (m && m.memo) ? m.memo : m).filter(Boolean);
  const memo = run.memo || last(memoRevs);
  let finals = run.final_verdicts || run.verdict_history || [];
  if (!finals.length && Array.isArray(run.memo_revisions)) finals = run.memo_revisions.map(m => m && m.verdict).filter(Boolean);
  let final = run.final_verdict || last(finals);
  if (typeof final === "string") final = { verdict: final };
  if (final && !finals.length) finals = [final];
  const scorecard = run.scorecard || (memo && memo.scorecard) || null;
  let decisions = run.human_decisions || (run.human_decision ? [run.human_decision] : []);
  if (!Array.isArray(decisions)) decisions = Object.values(decisions);
  const scores = (routing.scores || []).map(s => ({
    id: s.adviser_id, sim: Number(s.similarity ?? s.score ?? 0), selected: !!s.selected,
    reason: s.reason || "", cap: s.best_matching_capability || "",
  }));
  const selected = routing.selected || scores.filter(s => s.selected).map(s => s.id);
  let status = String(run.status || "").toLowerCase();
  if (!status) status = final ? "done" : (memo ? "checking" : positions.length ? "moderating" : "routing");
  const stageIdx = status === "idle" ? -1 : (status === "done" || status === "incomplete") ? 6 : status === "failed" ? -2 : Math.max(0, STAGES.findIndex(s => s.key === status));
  const posById = {}; positions.forEach(p => posById[p.adviser_id] = p);
  const blocking = positions.filter(p => (p.blocking_findings || []).length).map(p => p.adviser_id);

  // Normalize pipeline metrics (e.g. routing_time_s -> routing_s, total_time_s -> latency_s)
  const rawMetrics = run.metrics || {};
  const metrics = {
    ...rawMetrics,
    routing_s: rawMetrics.routing_s ?? rawMetrics.routing_time_s,
    advisers_s: rawMetrics.advisers_s ?? rawMetrics.advisers_time_s,
    moderating_s: rawMetrics.moderating_s ?? rawMetrics.moderating_time_s,
    scoring_s: rawMetrics.scoring_s ?? rawMetrics.scoring_time_s,
    writing_s: rawMetrics.writing_s ?? rawMetrics.writing_time_s,
    checking_s: rawMetrics.checking_s ?? rawMetrics.checking_time_s,
    latency_s: rawMetrics.latency_s ?? rawMetrics.total_time_s,
  };

  // Clean stringified dictionaries in blocking findings and blocked options if present
  positions.forEach(p => {
    if (Array.isArray(p.blocking_findings)) {
      p.blocking_findings.forEach(bf => {
        if (bf && typeof bf.text === "string" && (bf.text.startsWith("{'") || bf.text.startsWith('{"'))) {
          const m = bf.text.match(/['"]description['"]\s*:\s*['"](.*?)['"](?:\s*,\s*['"]fix['"]|\s*})/s) ||
                    bf.text.match(/['"]text['"]\s*:\s*['"](.*?)['"](?:\s*,\s*['"]fix['"]|\s*})/s);
          if (m && m[1]) bf.text = m[1].replace(/\\'/g, "'").replace(/\\"/g, '"');
        }
      });
    }
  });

  if (scorecard && scorecard.blocked_options) {
    Object.keys(scorecard.blocked_options).forEach(opt => {
      const arr = scorecard.blocked_options[opt];
      if (Array.isArray(arr)) {
        scorecard.blocked_options[opt] = arr.map(item => {
          if (typeof item === "string" && (item.startsWith("{'") || item.startsWith('{"'))) {
            const m = item.match(/['"]description['"]\s*:\s*['"](.*?)['"](?:\s*,\s*['"]fix['"]|\s*})/s) ||
                      item.match(/['"]text['"]\s*:\s*['"](.*?)['"](?:\s*,\s*['"]fix['"]|\s*})/s);
            if (m && m[1]) return m[1].replace(/\\'/g, "'").replace(/\\"/g, '"');
          }
          return item;
        });
      }
    });
  }

  return { run, id: run.run_id, brief, opts: brief.options || {}, routing, scores, selected, positions, posById, verdicts,
           memo, memoRevs, finals, final, scorecard, decisions, status, stageIdx, blocking, metrics,
           created: run.created_at, error: run.error };
}

/* ---------------- rendering ---------------- */
function renderHero(vm) {
  const b = vm.brief;
  const idle = vm.status === "idle";
  $("#qid").textContent = idle ? "No decision open" : (b.decision_id || "Decision");
  $("#question").textContent = idle ? "Bring a decision to the arena." : (b.question || "Untitled decision");
  $("#options").innerHTML = Object.entries(vm.opts).map(([k, v]) =>
    `<div class="opt ${k.toLowerCase()}"><i>${esc(k)}</i><span>${esc(v)}</span></div>`).join("");
  $("#constraints").innerHTML = (b.constraints || []).map(c => `<li>${esc(c)}</li>`).join("");
}

function renderSteps(vm) {
  const idx = state.stageIdx, failed = vm.status === "failed";
  const failStage = failed ? String(vm.run.failed_stage || vm.run.stage || "").toLowerCase() : "";
  const failIdx = failed ? Math.max(0, STAGES.findIndex(s => s.key === failStage)) : -1;
  $("#steps").innerHTML = STAGES.map((s, i) => {
    let cls = "";
    if (failed) cls = i < failIdx ? "done" : i === failIdx ? "failed" : "";
    else cls = i < idx || idx === 6 ? "done" : i === idx ? "now" : "";
    const t = vm.metrics[s.key + "_s"];
    return `<li class="${cls}"><span class="dot"></span><span>${s.label}</span><span class="t">${cls === "done" && t != null ? fmtDur(t) : ""}</span></li>`;
  }).join("");
  const err = $("#runError");
  if (failed) { err.hidden = false; err.innerHTML = `<b>Run stopped${failStage ? " at " + esc(failStage) : ""}.</b> ${esc(vm.error || vm.run.error_message || "See the server log for details.")}`; }
  else err.hidden = true;
}

function glyph(kind) {
  switch (kind) {
    case "rupee": return `<text class="glyph-t" y="8">₹</text>`;
    case "bars": return `<path class="glyph" d="M-10 9V2M-3 9V-3M4 9V-8M11 9V-1"/>`;
    case "person": return `<g class="glyph"><circle cx="0" cy="-5" r="5"/><path d="M-10 11c1.5-6 5-8.5 10-8.5s8.5 2.5 10 8.5"/></g>`;
    case "scales": return `<g class="glyph"><path d="M0-11V10M-7 10h14M-11-6h22"/><path d="M-11-6l-4 8h8zM11-6l-4 8h8z"/></g>`;
    case "shield": return `<path class="glyph" d="M0-12l10 4v7c0 7-5 11-10 13-5-2-10-6-10-13v-7z"/>`;
    case "gear": return `<g class="glyph"><circle r="5"/><circle r="10" stroke-dasharray="4 3.2"/></g>`;
  }
  return "";
}

function renderArena(vm) {
  const svg = $("#arena");
  const cx = 500, cy = 440;
  const tiers = [[250, 175], [340, 245], [440, 320]];
  let html = `<defs><radialGradient id="fg"><stop offset="0" stop-color="currentColor" stop-opacity="1"/><stop offset="1" stop-color="currentColor" stop-opacity="0"/></radialGradient></defs>`;
  tiers.forEach(([rx, ry], i) => { html += `<path class="tier ${i === 1 ? "t2" : ""}" d="M${cx - rx} ${cy} A${rx} ${ry} 0 0 1 ${cx + rx} ${cy}"/>`; });
  const ids = SEAT_ORDER.filter(id => ADVISERS[id]);
  vm.scores.forEach(s => { if (!ids.includes(s.id)) ids.push(s.id); });
  const n = ids.length, a0 = 166, a1 = 14;
  const seats = ids.map((id, i) => {
    const ang = (a0 - (a0 - a1) * (n === 1 ? .5 : i / (n - 1))) * Math.PI / 180;
    return { id, i, x: cx + 385 * Math.cos(ang), y: cy - 278 * Math.sin(ang) };
  });
  const tw = 360, th = 116, tx = cx - tw / 2, ty = cy - th - 6;
  html += `<ellipse class="glow-floor" cx="${cx}" cy="${ty + th / 2}" rx="260" ry="95" fill="url(#fg)"/>`;
  seats.forEach(s => {
    const sel = vm.selected.includes(s.id);
    const ex = cx + (s.x - cx) * .3, ey = ty + 4;
    html += `<path class="link ${sel ? "on" : ""}" style="--i:${s.i}" d="M${s.x.toFixed(1)} ${(s.y + 42).toFixed(1)} Q${((s.x + ex) / 2).toFixed(1)} ${(ey - 30).toFixed(1)} ${ex.toFixed(1)} ${ey.toFixed(1)}"/>`;
  });
  html += `<g class="dtile">
    <rect class="burst-ring" x="${tx}" y="${ty}" width="${tw}" height="${th}" rx="22"/>
    <rect class="burst-ring r2" x="${tx}" y="${ty}" width="${tw}" height="${th}" rx="22"/>
    <rect class="dt-orbit" x="${tx - 8}" y="${ty - 8}" width="${tw + 16}" height="${th + 16}" rx="28"/>
    <rect class="dt-bg" x="${tx}" y="${ty}" width="${tw}" height="${th}" rx="22"/>
    <text class="dt-id" id="dtId" x="${cx}" y="${ty + 28}"></text>
    <text class="dt-word" id="dtWord" x="${cx}" y="${ty + 66}"></text>
    <text class="dt-detail" id="dtDetail" x="${cx}" y="${ty + 94}"></text>
  </g>`;
  const C = 2 * Math.PI * 45;
  seats.forEach(s => {
    const sc = vm.scores.find(x => x.id === s.id);
    const sim = sc ? sc.sim : 0;
    const pos = vm.posById[s.id];
    const cls = ["seat"];
    if (vm.selected.includes(s.id)) cls.push("is-selected");
    if (pos) cls.push("has-pos", "stance-" + stanceKey(pos.stance));
    if (vm.blocking.includes(s.id)) cls.push("is-blocking");
    const off = C * (1 - Math.max(0, Math.min(1, sim)));
    const sub = !sc ? `<tspan>${vm.status === "idle" ? "waiting" : "not scored"}</tspan>` : (pos ? `<tspan class="sub-st">${stanceShort(stanceKey(pos.stance))}  </tspan>` : "") + `<tspan>${sim.toFixed(2)}</tspan>`;
    html += `<g class="${cls.join(" ")}" style="--i:${s.i}" data-adv="${esc(s.id)}" transform="translate(${s.x.toFixed(1)} ${s.y.toFixed(1)})" tabindex="0" role="button" aria-label="${esc(advName(s.id))}${sc ? ", routing score " + sim.toFixed(2) : ""}">
      <circle class="score" r="45" stroke-dasharray="${C.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}" transform="rotate(-90)"/>
      <circle class="spin" r="45"/>
      <circle class="ring" r="37"/>
      <circle class="body" r="33"/>
      ${glyph((ADVISERS[s.id] || {}).glyph)}
      <g transform="translate(29 -29)"><g class="shield"><path d="M0-11l9 3.5v6c0 6.5-4.5 10-9 12-4.5-2-9-5.5-9-12v-6z"/><text y="4">!</text></g></g>
      <g transform="translate(28 27)"><g class="tick"><circle r="10"/><path d="M-4.5 0.5l3 3 6-6.5"/></g></g>
      <text class="name" y="70">${esc(advName(s.id, true))}</text>
      <text class="sub" y="88">${sub}</text>
    </g>`;
  });
  svg.innerHTML = html;
  $$(".seat", svg).forEach(g => {
    const id = g.dataset.adv;
    g.addEventListener("click", () => focusCard(id));
    g.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); focusCard(id); } });
    g.addEventListener("mouseenter", () => highlight(id, true));
    g.addEventListener("mouseleave", () => highlight(id, false));
  });
}
function stanceShort(k) { return k === "a" ? "backs A" : k === "b" ? "backs B" : k === "c" ? "conditional" : "unsure"; }

/* ---------------- arena state: idle / in session / closed / stopped ---------------- */
const LIVE_TEXT = ["Choosing the advisers", "Advisers are arguing their case", "The moderator is checking every claim",
                   "Scoring the options", "The Scribe is drafting the memo", "Final check on the memo"];
function setArena(kind, stageIdx) {
  const vm = state.vm, panel = $("#arenaPanel");
  panel.classList.remove("a-idle", "a-live", "a-done", "a-failed", "v-pass", "v-incomplete", "v-wrong");
  panel.classList.add("a-" + kind);
  const verdict = upper(vm && vm.final && vm.final.verdict) || "PASS";
  if (kind === "done") panel.classList.add("v-" + verdict.toLowerCase());
  const id = (vm && vm.brief && vm.brief.decision_id) || "";
  let head = "", detail = "", word = "", sub = "";
  if (kind === "idle") { head = "Arena idle"; detail = "No debate in session"; word = "No session"; sub = "Start a new decision or open a saved run"; }
  else if (kind === "live") { const t = LIVE_TEXT[Math.max(0, Math.min(5, stageIdx))]; head = "In session"; detail = t; word = "Debating"; sub = t; }
  else if (kind === "failed") { const st = String((vm && (vm.run.failed_stage || vm.run.stage)) || "").toLowerCase(); head = "Run stopped"; detail = st ? "at " + st : "See the progress panel"; word = "Stopped"; sub = detail; }
  else {
    const k = stanceKey(vm && vm.memo && vm.memo.stance);
    head = "Session closed"; detail = `Verdict ${verdict}, ready for sign-off`; word = verdict;
    sub = k === "c" ? "Conditional: move to option B in phases".replace("option B", vm.memo && /option a/i.test(vm.memo.recommendation || "") ? "option A" : "option B")
        : k === "b" ? "Recommends option B" : k === "a" ? "Recommends option A" : "Recommendation ready";
  }
  $("#aState").textContent = head; $("#aDetail").textContent = detail;
  const w = $("#dtWord"); if (w) { $("#dtId").textContent = id; w.textContent = word; $("#dtDetail").textContent = sub; w.style.fontSize = word.length > 9 ? "21px" : ""; }
  $("#aTimer").textContent = kind === "idle" ? "" : $("#clock").textContent;
}
function burst() { const p = $("#arenaPanel"); p.classList.remove("burst"); void p.getBoundingClientRect(); p.classList.add("burst"); setTimeout(() => p.classList.remove("burst"), 1700); }
function arenaKindFor(vm) { if (!vm || vm.status === "idle") return "idle"; if (vm.status === "done") return "done"; if (vm.status === "failed") return "failed"; return "live"; }

function highlight(id, on) {
  $$(`[data-adv="${CSS.escape(id)}"]`).forEach(el => el.classList.toggle("hl", on));
}
function focusCard(id) {
  const card = $(`#card-${CSS.escape(id)}`);
  if (!card) return;
  card.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "center" });
  card.classList.add("hl"); setTimeout(() => card.classList.remove("hl"), 1600);
}

function renderRouting(vm) {
  const rows = [...vm.scores].sort((a, b) => b.sim - a.sim);
  const top = rows.length ? rows[0].sim : 0;
  const margin = Math.max(0, top - ROUTING.margin);
  $("#routeRows").innerHTML = rows.map(r => {
    const rule = /rule/i.test(r.reason);
    return `<div class="r-row" data-adv="${esc(r.id)}">
      <div class="r-top"><b>${esc(advName(r.id))}</b><span class="v tabnum">${r.sim.toFixed(2)}</span></div>
      <div class="track" title="Dashed lines: selection floor ${ROUTING.floor} and margin ${margin.toFixed(2)}"><i style="--w:${(r.sim * 100).toFixed(1)}%"></i>
        <span class="mark" style="left:${ROUTING.floor * 100}%"></span><span class="mark" style="left:${margin * 100}%"></span></div>
      <div class="r-why ${rule ? "rule" : ""}" title="${esc(r.cap ? "Closest capability: " + r.cap : "")}">${r.selected ? "Selected" : "Not selected"}${r.reason ? ": " + esc(r.reason) : ""}</div>
    </div>`;
  }).join("") || `<p class="muted">Routing hasn't run yet.</p>`;
  $$("#routeRows .r-row").forEach(r => {
    r.addEventListener("mouseenter", () => highlight(r.dataset.adv, true));
    r.addEventListener("mouseleave", () => highlight(r.dataset.adv, false));
  });
  $("#routeQuery").textContent = vm.routing.routing_query || ""; $(".rq").hidden = !vm.routing.routing_query;
}

function renderVerdict(vm) {
  const box = $("#verdict");
  const v = upper(vm.final && vm.final.verdict) || "PENDING";
  box.classList.remove("v-pass", "v-incomplete", "v-wrong");
  box.classList.add("v-" + v.toLowerCase());
  const sw = $("#sealWord"); sw.textContent = v; sw.style.fontSize = v.length > 6 ? "13px" : v.length > 4 ? "19px" : "";
  const m = vm.memo || {};
  $("#rec").textContent = m.recommendation || "No recommendation yet.";
  const k = stanceKey(m.stance);
  const revs = Math.max(0, vm.finals.length - 1);
  const revText = revs === 0 ? "Passed the final check first time" : revs === 1 ? "Sent back once by the moderator, then passed" : `Sent back ${revs} times by the moderator`;
  const verdictText = v === "PASS" ? revText : v === "INCOMPLETE" ? "The moderator found gaps it could not close. Read the issues before signing." : v === "WRONG" ? "The moderator found errors. Don't act on this memo." : "";
  $("#vmeta").innerHTML = `<span class="stance-pill sp-${k}">${esc(k === "c" ? "Conditional recommendation" : stanceLabel(k, vm.opts))}</span>
    <span>${esc(verdictText)}</span>`;
  const conf = m.confidence != null ? Math.round(m.confidence * 100) + "%" : "–";
  const dur = vm.metrics.latency_s ? fmtDur(vm.metrics.latency_s) : "";
  $("#vside").innerHTML = `<div class="big tabnum">${conf}<small>confidence</small></div>
    ${dur ? `<div class="muted" style="font-size:13px">Debated in ${dur}${vm.metrics.facts_considered ? `, ${vm.metrics.facts_considered} facts considered` : ""}</div>` : ""}`;
}

function renderCards(vm) {
  const order = [...vm.scores].sort((a, b) => b.sim - a.sim).map(s => s.id);
  vm.positions.forEach(p => { if (!order.includes(p.adviser_id)) order.push(p.adviser_id); });
  let kept = 0, flagged = 0, struck = 0;
  const cards = order.filter(id => vm.posById[id]).map(id => {
    const p = vm.posById[id];
    const k = stanceKey(p.stance);
    let ck = 0, cf = 0, cx = 0;
    const claims = (p.claims || []).map(c => {
      const v = vm.verdicts[c.claim_id];
      const st = (upper(v && v.status) || "KEEP");
      const cls = st === "STRIKE" ? "strike" : st === "FLAG" ? "flag" : "keep";
      if (cls === "keep") ck++; else if (cls === "flag") cf++; else cx++;
      const calc = c.formula ? `<span class="calc">${esc(c.formula)}${c.value != null ? " = " + esc(fmtNum(c.value, c.unit)) : ""}</span>` : "";
      const label = cls === "strike" ? "Struck by the moderator: " : cls === "flag" ? "Flagged: " : "";
      return `<li class="claim ${cls}"><span class="st" aria-hidden="true"></span><div><span class="txt">${esc(c.text)}</span>${calc}
        ${v && cls !== "keep" ? `<span class="why">${label}${esc(v.reason)}</span>` : ""}
        <div class="chips">${(c.evidence_ids || []).map(chip).join("")}</div></div></li>`;
    }).join("");
    kept += ck; flagged += cf; struck += cx;
    const blocks = (p.blocking_findings || []).map((b, i) => {
      const bv = vm.verdicts[`${id}-B${i + 1}`];
      return `<div class="blocker"><h4><svg width="14" height="16" viewBox="-10 -12 20 26"><path d="M0-11l9 3.5v6c0 6.5-4.5 10-9 12-4.5-2-9-5.5-9-12v-6z" fill="currentColor"/></svg>Blocking finding</h4>
        <div>${esc(b.text)}</div>
        <div class="fix"><b>Fix that clears it:</b> ${esc(b.fix)}</div>
        <div class="chips" style="display:flex;gap:6px;flex-wrap:wrap">${(b.evidence_ids || []).map(chip).join("")}</div>
        ${bv ? `<div class="muted" style="font-size:12.5px">Moderator: ${esc(bv.reason)}</div>` : ""}</div>`;
    }).join("");
    const crit = {};
    (p.criterion_scores || []).forEach(s => { (crit[s.criterion] = crit[s.criterion] || {})[s.option] = s.score; });
    const scoresRow = Object.entries(crit).map(([c, o]) => `<span>${esc(c)}: ${Object.entries(o).map(([opt, sc]) => `${esc(opt)} <b>${esc(sc)}</b>`).join(" · ")}</span>`).join("");
    const conf = Math.round((p.confidence || 0) * 100);
    return `<article class="panel card" id="card-${esc(id)}" data-adv="${esc(id)}">
      <div class="card-h">
        <div class="avatar" style="border-color:var(--${k === "a" ? "opt-a" : k === "b" ? "opt-b" : k === "c" ? "cond" : "line-2"})"><svg viewBox="-16 -16 32 32">${glyph((ADVISERS[id] || {}).glyph).replace('class="glyph-t" y="8"', 'class="glyph-t" y="7" style="fill:var(--ink);font:600 17px var(--f-display);text-anchor:middle"').replace(/class="glyph"/g, 'fill="none" stroke="var(--ink)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"')}</svg></div>
        <div><h3>${esc(advName(id))}</h3><div class="conf">Confidence <span class="meter"><i style="width:${conf}%"></i></span> ${conf}%</div></div>
        <span class="stance-pill sp-${k}">${esc(stanceLabel(k, vm.opts))}</span>
      </div>
      <p class="summary">${esc(p.summary)}</p>
      ${blocks}
      <div class="tally"><span class="k"><b>${ck}</b> kept</span><span class="f"><b>${cf}</b> flagged</span><span class="x"><b>${cx}</b> struck</span></div>
      <ul class="claims">${claims}</ul>
      ${scoresRow ? `<div class="scores-row">${scoresRow}</div>` : ""}
    </article>`;
  });
  $("#cards").innerHTML = cards.join("") || `<p class="muted">No adviser positions yet.</p>`;
  const nAdv = cards.length;
  $("#debateTally").textContent = nAdv ? `${nAdv} advisers, ${kept + flagged + struck} claims: ${kept} kept, ${flagged} flagged, ${struck} struck by the moderator.` : "";
  $$("#cards .card").forEach(c => {
    c.addEventListener("mouseenter", () => highlight(c.dataset.adv, true));
    c.addEventListener("mouseleave", () => highlight(c.dataset.adv, false));
  });
}

function renderScorecard(vm) {
  const sc = vm.scorecard;
  if (!sc || !(sc.rows || []).length) { $("#sc").innerHTML = `<p class="muted">No scorecard yet.</p>`; return; }
  const opts = Object.keys(vm.opts).length ? Object.keys(vm.opts) : Object.keys(sc.weighted_totals || {});
  const owners = {};
  (vm.brief.criteria || []).forEach(c => owners[c.name] = c.owners || []);
  const totals = sc.weighted_totals || {};
  const best = opts.reduce((b, o) => (totals[o] ?? -1) > (totals[b] ?? -1) ? o : b, opts[0]);
  const rows = sc.rows.map(r => {
    const ow = r.owners || owners[r.criterion] || [];
    return `<tr><td><div>${esc(r.criterion)}</div><div class="owners">${ow.map(o => esc(advName(o, true))).join(", ")}${(r.missing_owners || []).length ? ` · missing: ${r.missing_owners.map(o => esc(advName(o, true))).join(", ")}` : ""}</div></td>
      <td class="num tabnum" style="text-align:left">${Math.round((r.weight || 0) * 100)}%</td>
      ${opts.map(o => { const s = r.scores ? r.scores[o] : null; return `<td><div class="sbar ${o.toLowerCase()}"><div class="track"><i style="--w:${s == null ? 0 : (s / 5 * 100)}%"></i></div><span class="tabnum">${s == null ? "–" : esc(s)}</span></div></td>`; }).join("")}
      <td>${(r.evidence_ids || []).slice(0, 3).map(chip).join(" ")}</td></tr>`;
  }).join("");
  const blocked = sc.blocked_options || {};
  const notes = Object.entries(blocked).filter(([, v]) => (v || []).length).map(([o, v]) =>
    `<div class="blocked-note"><b>Option ${esc(o)} is blocked</b><span>${v.map(esc).join(" ")} The recommendation must clear this with a condition, or a person must override it.</span></div>`).join("");
  $("#sc").innerHTML = `<div class="table-wrap"><table>
    <thead><tr><th>Criterion and owners</th><th>Weight</th>${opts.map(o => `<th>Option ${esc(o)}</th>`).join("")}<th>Evidence</th></tr></thead>
    <tbody>${rows}
    <tr class="total"><td><b style="font-weight:500">Weighted total</b><div class="owners">Out of 5</div></td><td></td>
      ${opts.map(o => `<td><span class="tot tabnum ${o === best ? "win" : ""}">${totals[o] != null ? Number(totals[o]).toFixed(2) : "–"}</span>${blocked[o] && blocked[o].length ? ` <span class="pill low">blocked</span>` : ""}</td>`).join("")}<td></td></tr>
    </tbody></table></div>${notes}`;
}

function renderMemo(vm) {
  const m = vm.memo;
  if (!m) { $("#memoBody").innerHTML = `<p class="muted">No memo yet.</p>`; return; }
  const condItems = (m.conditions || []).map(c => { const w = withChips(c); return `<li><div>${esc(w.text)}<div class="chips" style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px;align-items:center">${w.ids.map(chip).join("")}${byLine(w)}</div></div></li>`; }).join("");
  const planItems = (m.phased_plan || []).map(c => { const w = withChips(c); return `<li><div>${esc(w.text)} ${w.ids.map(chip).join(" ")}</div></li>`; }).join("");
  const nums = (m.key_numbers || []).map(k => `<tr><td>${esc(k.text)}<div class="f">${k.formula ? esc(k.formula) : ""}</div></td>
     <td class="num tabnum">${esc(fmtNum(k.value, k.unit))}</td><td>${(k.evidence_ids || []).map(chip).join(" ")}</td></tr>`).join("");
  const dissent = (m.dissent || []).map(d => { const w = withChips(d); const mm = w.text.match(/^([A-Za-z&\s]+?)(:|\s(favours|wants|says))/);
    return `<li>${mm ? `<b>${esc(mm[1])}</b>${esc(w.text.slice(mm[1].length))}` : esc(w.text)} ${w.ids.map(chip).join(" ")}</li>`; }).join("");
  const watch = (m.numbers_to_watch || []).map(d => { const w = withChips(d); return `<li>${esc(w.text)} ${w.ids.map(chip).join(" ")}</li>`; }).join("");
  const drafts = vm.finals.map((f, i) => {
    const v = upper(f.verdict);
    const issue = (f.issues || [])[0];
    const txt = v === "PASS" ? "Makes sense, complete and correct." : issue ? issue.detail : (f.revision_instructions || "");
    return `<div class="draft"><span class="muted code">Draft ${i + 1}</span><span class="vtag ${esc(v)}">${esc(v)}</span><span>${esc(txt)}</span></div>`;
  }).join("");
  $("#memoBody").innerHTML = `
    <div class="panel box"><h3>Conditions before any volume moves</h3><ul class="conds">${condItems || "<li>No conditions.</li>"}</ul></div>
    <div class="panel box"><h3>Phased plan</h3><ol class="plan">${planItems || "<li><div>Single phase.</div></li>"}</ol></div>
    <div class="panel box"><h3>Key numbers</h3><div class="table-wrap"><table><thead><tr><th>What</th><th style="text-align:right">Value</th><th>Evidence</th></tr></thead><tbody>${nums}</tbody></table></div></div>
    <div class="panel box"><h3>Dissent on record</h3><ul class="dissent">${dissent || "<li>No adviser disagreed.</li>"}</ul>
      <h3 style="margin-top:8px">Numbers to watch</h3><ul class="watch">${watch}</ul>
      ${drafts ? `<h3 style="margin-top:8px">Moderator's final check</h3><div class="drafts">${drafts}</div>` : ""}</div>`;
}

function renderLog(vm) {
  const items = vm.decisions.map(d => {
    const act = String(d.action || "").toLowerCase() === "override" ? "Overrode" : "Approved";
    return `<li><div><span class="act">${act}</span> by <span class="who">${esc(d.decided_by || "Unnamed")}</span>
      <div class="when">${esc(fmtTime(d.decided_at))}</div>${(d.reason || d.note) ? `<div class="muted" style="margin-top:4px">${esc(d.reason || d.note)}</div>` : ""}</div></li>`;
  });
  $("#log").innerHTML = items.length ? items.join("") : `<li style="grid-template-columns:1fr" class="muted">No one has signed this decision yet. The record keeps every approval and override.</li>`;
}

function applyStage(idx, working) {
  state.stageIdx = idx;
  const root = $("#top");
  for (let i = 0; i <= 6; i++) root.classList.toggle("s" + i, idx >= i);
  root.classList.toggle("working", !!working);
  $$("[data-from]").forEach(sec => sec.classList.toggle("pending", idx < Number(sec.dataset.from)));
  const idle = !state.vm || state.vm.status === "idle";
  $$(".wait").forEach(w => { if (!w.dataset.orig) w.dataset.orig = w.innerHTML; w.innerHTML = idle && !state.replaying ? "Nothing here yet. Start a new decision or open a saved run." : w.dataset.orig; });
  $("#replayBtn").disabled = idle || state.replaying;
  if (state.vm) renderSteps(state.vm);
}

function renderAll(vm) {
  const safe = (name, fn) => { try { fn(vm); } catch (e) { console.error("[colosseum] " + name + " failed", e); } };
  safe("hero", renderHero); safe("arena", renderArena); safe("routing", renderRouting);
  safe("verdict", renderVerdict); safe("cards", renderCards); safe("scorecard", renderScorecard);
  safe("memo", renderMemo); safe("log", renderLog);
  const idx = vm.status === "failed" ? Math.max(1, ...[vm.positions.length ? 2 : 0]) : vm.stageIdx;
  applyStage(idx, vm.status === "advisers");
  updateClock();
  if (!state.replaying) setArena(arenaKindFor(vm), vm.stageIdx);
}

/* ---------------- clock ---------------- */
let clockTimer = null;
function updateClock() {
  const vm = state.vm; if (!vm) return;
  let s = vm.metrics.latency_s;
  if (vm.status !== "done" && vm.status !== "failed" && vm.created) s = (Date.now() - new Date(vm.created).getTime()) / 1000;
  $("#clock").textContent = fmtClock(s || 0);
  const at = $("#aTimer"); if (at && arenaKindFor(vm) !== "idle") at.textContent = $("#clock").textContent;
}

/* ---------------- replay: the one orchestrated moment ---------------- */
async function replay() {
  if (!state.vm || state.replaying) return;
  if (reduced) { toast("Replay is off because your system asks for reduced motion."); return; }
  state.replaying = true; $("#replayBtn").disabled = true; $$(".wait").forEach(w => { if (w.dataset.orig) w.innerHTML = w.dataset.orig; });
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const t0 = performance.now(); const total = state.vm.metrics.latency_s || 0;
  const tick = setInterval(() => { $("#clock").textContent = fmtClock(Math.min(total, (performance.now() - t0) / 9000 * total)); }, 80);
  const step = (i, working, ms) => { applyStage(i, working); setArena("live", Math.max(0, i)); $("#aTimer").textContent = $("#clock").textContent; return sleep(ms); };
  await step(-1, false, 500);
  await step(0, false, 1300);
  await step(1, true, 2100);
  await step(2, false, 1200);
  await step(3, false, 1500);
  await step(4, false, 1000);
  await step(5, false, 1000);
  applyStage(6, false);
  clearInterval(tick); updateClock();
  state.replaying = false; $("#replayBtn").disabled = false;
  setArena(arenaKindFor(state.vm), 6); burst();
}

/* a short, visible entrance whenever a finished run is opened */
async function entrance() {
  const vm = state.vm; if (!vm || vm.status !== "done") return;
  if (reduced) { applyStage(6); setArena("done", 6); return; }
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const token = state.entranceToken = (state.entranceToken || 0) + 1;
  applyStage(-1); setArena("live", 0);
  await sleep(60); if (token !== state.entranceToken) return;
  applyStage(0); await sleep(500); if (token !== state.entranceToken) return;
  applyStage(1, true); setArena("live", 1); await sleep(700); if (token !== state.entranceToken) return;
  applyStage(3); setArena("live", 2); await sleep(600); if (token !== state.entranceToken) return;
  applyStage(6); setArena("done", 6); burst();
}

/* ---------------- evidence drawer ---------------- */
function openDrawer(title, html) {
  $("#dTitle").textContent = title; $("#dBody").innerHTML = html;
  $("#drawer").classList.add("on"); $("#scrim").classList.add("on"); $("#drawer").setAttribute("aria-hidden", "false");
  setTimeout(() => $("#dClose").focus(), 50);
}
function closeDrawer() { $("#drawer").classList.remove("on"); $("#scrim").classList.remove("on"); $("#drawer").setAttribute("aria-hidden", "true"); }

async function getFact(id) {
  if (state.mode === "live") {
    try { const r = await fetch(`/facts/${encodeURIComponent(id)}`); if (r.ok) { const f = await r.json(); FACTS[id] = { ...(FACTS[id] || {}), ...f }; return FACTS[id]; } } catch (e) {}
  }
  return FACTS[id];
}
async function showEvidence(id) {
  if (id === "BRIEF") {
    const b = state.vm ? state.vm.brief : {};
    openDrawer("Evidence", `<div class="fid">BRIEF</div><p class="fstmt">${esc(b.question || "")}</p><p class="muted">Numbers quoted from the decision brief itself.</p>`);
    return;
  }
  const f = await getFact(id);
  const citing = [];
  (state.vm ? state.vm.positions : []).forEach(p => (p.claims || []).forEach(c => { if ((c.evidence_ids || []).includes(id)) citing.push({ who: advName(p.adviser_id, true), c, v: state.vm.verdicts[c.claim_id] }); }));
  if (!f) {
    openDrawer("Evidence", `<div class="fid">${esc(id)}</div><p class="muted">This evidence ID isn't in the fact store. Document passages and live data appear here once the API serves them.</p>`);
    return;
  }
  const val = f.value_num != null && f.value_num !== "" ? fmtNum(f.value_num, "") : (f.value_text || "");
  openDrawer("Evidence", `
    <div class="fid">${esc(f.fact_id || id)}</div>
    <div class="fstate"><span class="pill ${esc(f.reliability)}">${esc(f.reliability || "unknown")} reliability</span><span class="pill">${esc(advName(f.domain))}</span>${f.topic ? `<span class="pill">${esc(f.topic)}</span>` : ""}</div>
    ${f.superseded_by ? `<div class="old-banner">Out of date. Replaced by ${chip(f.superseded_by)}</div>` : ""}
    <p class="fstmt">${esc(f.statement)}</p>
    ${val ? `<div class="fval tabnum">${esc(val)}<small>${esc(f.unit || "")}</small></div>` : ""}
    <dl class="dl"><dt>Source</dt><dd>${esc(f.source_ref)}</dd><dt>Source type</dt><dd>${esc(f.source_type)}</dd><dt>As of</dt><dd>${esc(f.as_of)}</dd><dt>About</dt><dd>${esc(f.entity)}</dd></dl>
    ${citing.length ? `<div class="cited"><b style="font-weight:500;color:var(--ink)">Cited in this debate</b>${citing.map(x => `<div><b style="font-weight:500">${esc(x.who)}</b>${x.v && upper(x.v.status) !== "KEEP" ? ` <span class="vtag ${upper(x.v.status) === "STRIKE" ? "WRONG" : "INCOMPLETE"}">${upper(x.v.status) === "STRIKE" ? "STRUCK" : "FLAGGED"}</span>` : ""}<br>${esc(x.c.text)}</div>`).join("")}</div>` : ""}`);
}

/* ---------------- new decision ---------------- */
async function openNewDecision() {
  let brief = state.vm ? state.vm.brief : SAMPLE.brief;
  if (state.mode === "live") { try { const r = await fetch("/briefs/sample"); if (r.ok) brief = await r.json(); } catch (e) {} }
  const crit = (brief.criteria || []).map((c, i) => `<div class="crit-row"><input class="cname" id="cn${i}" value="${esc(c.name)}" aria-label="Criterion name"><input class="cw tabnum" id="cw${i}" type="number" min="0" max="100" value="${Math.round(c.weight * 100)}" aria-label="Weight percent"></div>`).join("");
  openDrawer("New decision", `
    <p class="muted">${state.mode === "live" ? "Starts a real debate. Expect about 5 to 8 minutes." : "This preview isn't connected to the Colosseum API, so it replays the sample debate instead."}</p>
    <div class="field"><label for="nq">Question</label><textarea id="nq">${esc(brief.question)}</textarea></div>
    <div class="field"><label for="nA">Option A</label><input id="nA" value="${esc((brief.options || {}).A)}"></div>
    <div class="field"><label for="nB">Option B</label><input id="nB" value="${esc((brief.options || {}).B)}"></div>
    <div class="field"><label for="nc">Constraints, one per line</label><textarea id="nc">${esc((brief.constraints || []).join("\n"))}</textarea></div>
    <div class="field"><label for="nx">Context</label><textarea id="nx">${esc(brief.context || "")}</textarea></div>
    <div class="field"><span>Criteria and weights (%)</span><div class="crit-grid">${crit}</div><span class="wsum" id="wsum"></span></div>
    <button class="btn primary" id="startBtn" type="button">Start the debate</button>`);
  const sumCheck = () => { const t = $$(".cw").reduce((a, i) => a + (Number(i.value) || 0), 0); const el = $("#wsum"); el.textContent = `Weights add up to ${t}%` + (t === 100 ? "" : ". They must add up to 100%."); el.className = "wsum " + (t === 100 ? "ok" : "bad"); $("#startBtn").disabled = t !== 100; };
  $$(".cw").forEach(i => i.addEventListener("input", sumCheck)); sumCheck();
  $("#startBtn").addEventListener("click", async () => {
    const nb = JSON.parse(JSON.stringify(brief));
    nb.question = $("#nq").value.trim(); nb.options = { A: $("#nA").value.trim(), B: $("#nB").value.trim() };
    nb.constraints = $("#nc").value.split("\n").map(s => s.trim()).filter(Boolean); nb.context = $("#nx").value.trim();
    nb.criteria = (nb.criteria || []).map((c, i) => ({ ...c, name: $("#cn" + i).value.trim() || c.name, weight: (Number($("#cw" + i).value) || 0) / 100 }));
    if (state.mode !== "live") { closeDrawer(); toast("Replaying the sample debate."); window.scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" }); replay(); return; }
    $("#startBtn").disabled = true; $("#startBtn").textContent = "Starting…";
    try {
      const r = await fetch("/decisions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(nb) });
      if (!r.ok) throw new Error(`The API answered ${r.status}. ${await r.text()}`);
      const { run_id } = await r.json();
      closeDrawer(); toast("Debate started. Advisers are joining.");
      await loadRunList(); await openRun(run_id);
    } catch (e) {
      $("#startBtn").disabled = false; $("#startBtn").textContent = "Start the debate";
      toast("Couldn't start the debate: " + e.message);
    }
  });
}

/* ---------------- sign-off ---------------- */
async function signOff(action) {
  const vm = state.vm; if (!vm) return;
  const name = $("#signName").value.trim(), reason = $("#signReason").value.trim();
  const err = $("#signErr");
  if (!name) { err.textContent = "Add your name and role so the record shows who decided."; $("#signName").focus(); return; }
  if (action === "override" && !reason) { err.textContent = "An override needs a reason. Say what you decided instead and why."; $("#signReason").focus(); return; }
  err.textContent = "";
  const entry = { action, reason, note: reason, decided_by: name, decided_at: new Date().toISOString() };
  if (state.mode === "live") {
    try {
      const r = await fetch(`/runs/${encodeURIComponent(vm.id)}/decision`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action, reason, decided_by: name }) });
      if (!r.ok) throw new Error(`The API answered ${r.status}`);
    } catch (e) { err.textContent = "Couldn't save the decision: " + e.message + ". Try again."; return; }
  }
  vm.decisions.push(entry); renderLog(vm);
  $("#signReason").value = "";
  toast((action === "override" ? "Override recorded" : "Approval recorded") + (state.mode === "live" ? "." : " in this preview only."));
}

/* ---------------- toast ---------------- */
let toastT;
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("on"); clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("on"), 3600); }

/* ---------------- data loading ---------------- */
async function fetchJSON(url, ms = 4000) {
  const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), ms);
  try { const r = await fetch(url, { signal: ctl.signal, headers: { Accept: "application/json" } });
        if (!r.ok) throw new Error(String(r.status));
        const ct = r.headers.get("content-type") || ""; if (!ct.includes("json")) throw new Error("not json");
        return await r.json(); }
  finally { clearTimeout(t); }
}
async function detectApi() {
  if (/(^|[?#&])demo\b/.test(location.search + location.hash)) return false;
  try { const runs = await fetchJSON("/runs?limit=25", 2500); return Array.isArray(runs) ? runs : Array.isArray(runs.runs) ? runs.runs : false; }
  catch (e) { return false; }
}
async function loadRunList() {
  try { const r = await fetchJSON("/runs?limit=25"); state.runs = Array.isArray(r) ? r : (r.runs || []); } catch (e) { console.warn("[colosseum] run list failed", e); }
  fillPicker();
}
function fillPicker() {
  const p = $("#picker");
  const idleOpt = `<option value="">No run open (idle arena)</option>`;
  if (state.mode !== "live") { p.innerHTML = idleOpt + `<option value="sample">DEC-001 sample run (Legion Capital)</option>`; p.value = state.runId === "" ? "" : "sample"; return; }
  p.innerHTML = idleOpt + state.runs.map(r => {
    const fv = r.final_verdict; const v = upper(typeof fv === "string" ? fv : fv && fv.verdict) || upper(r.status);
    return `<option value="${esc(r.run_id)}">${esc(fmtTime(r.created_at))}  ${esc(r.run_id.replace(/^run_/, ""))}  ${esc(v)}</option>`;
  }).join("") + `<option value="sample">Sample run (offline)</option>`;
  p.value = state.runId || "";
}
const IDLE_RUN = { status: "idle", brief: { decision_id: "", question: "", options: {}, constraints: [] } };
async function openRun(id) {
  clearInterval(state.poll); state.poll = null; clearInterval(clockTimer); clockTimer = null;
  state.runId = id;
  if (!id) { state.vm = toViewModel(IDLE_RUN); renderAll(state.vm); fillPicker(); $("#picker").value = ""; return; }
  if (id === "sample" || state.mode !== "live") { state.vm = toViewModel(SAMPLE); renderAll(state.vm); fillPicker(); $("#picker").value = "sample"; entrance(); return; }
  try {
    const run = await fetchJSON(`/runs/${encodeURIComponent(id)}`, 8000);
    state.vm = toViewModel(run); renderAll(state.vm); fillPicker(); entrance();
    if (!["done", "failed"].includes(state.vm.status)) {
      clockTimer = setInterval(updateClock, 1000);
      state.poll = setInterval(async () => {
        try {
          const nr = await fetchJSON(`/runs/${encodeURIComponent(id)}`, 8000);
          const nvm = toViewModel(nr); state.vm = nvm; renderAll(nvm);
          if (["done", "failed"].includes(nvm.status)) { if (nvm.status === "done") burst(); clearInterval(state.poll); clearInterval(clockTimer); loadRunList(); toast(nvm.status === "done" ? "Debate finished. The memo is ready for sign-off." : "The run stopped. See the progress panel."); }
        } catch (e) { console.warn("[colosseum] poll failed", e); }
      }, 3000);
    }
  } catch (e) {
    toast("Couldn't open that run (" + e.message + "). Showing the sample instead.");
    state.vm = toViewModel(SAMPLE); renderAll(state.vm);
  }
}

/* ---------------- wiring ---------------- */
function initTheme() {
  const btn = $("#themeBtn");
  let saved = null; try { saved = localStorage.getItem("colosseum-theme"); } catch (e) {}
  if (saved) document.documentElement.setAttribute("data-theme", saved);
  btn.addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("colosseum-theme", next); } catch (e) {}
  });
}
function initEvents() {
  document.addEventListener("click", e => { const c = e.target.closest("[data-ev]"); if (c) { e.preventDefault(); showEvidence(c.dataset.ev); } });
  $("#dClose").addEventListener("click", closeDrawer); $("#scrim").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeDrawer(); });
  $("#replayBtn").addEventListener("click", () => { window.scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" }); replay(); });
  $("#newBtn").addEventListener("click", openNewDecision);
  $("#picker").addEventListener("change", e => openRun(e.target.value));
  $("#signForm").addEventListener("submit", e => { e.preventDefault(); signOff("approve"); });
  $("#overrideBtn").addEventListener("click", () => signOff("override"));
}

async function loadInitialData() {
  try {
    const [factsRes, sampleRes] = await Promise.all([
      fetch("data/facts.json"),
      fetch("data/sample_run.json")
    ]);
    if (factsRes.ok) FACTS = await factsRes.json();
    if (sampleRes.ok) SAMPLE = await sampleRes.json();
  } catch (err) {
    console.warn("[colosseum] Could not load static data from data/ directory:", err);
  }
}

async function boot() {
  try { initTheme(); } catch (e) { console.error(e); }
  try { initEvents(); } catch (e) { console.error(e); }
  await loadInitialData();
  // render the sample at once so the page is never empty, then switch to live data if the API answers
  state.vm = toViewModel(SAMPLE); state.runId = "sample"; renderAll(state.vm); fillPicker();
  setTimeout(entrance, 250);
  const runs = await detectApi();
  if (runs) {
    state.mode = "live"; state.runs = runs;
    const m = $("#mode"); m.textContent = "Live"; m.classList.add("live");
    const pick = runs.find(r => String(r.status).toLowerCase() === "done") || runs[0];
    if (pick) await openRun(pick.run_id); else fillPicker();
  }
}
boot();
})();
