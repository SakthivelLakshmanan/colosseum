(() => {
"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const sleep = ms => new Promise(r => setTimeout(r, ms));

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
  { key: "routing", label: "Who joined", sheet: "routing", live: "Choosing the advisers" },
  { key: "advisers", label: "Their arguments", sheet: "debate", live: "Advisers are arguing their case" },
  { key: "moderating", label: "Fact checks", sheet: "checks", live: "The moderator is checking every claim" },
  { key: "scoring", label: "Scores", sheet: "scorecard", live: "Scoring the options" },
  { key: "writing", label: "Recommendation", sheet: "memo", live: "The Scribe is drafting the memo" },
  { key: "checking", label: "Quality check", sheet: "final", live: "Final check on the memo" },
  { key: "done", label: "Your decision", sheet: "sign", live: "" },
];
const ROUTING = { floor: 0.55, margin: 0.06 };
const GEO = { W: 1200, H: 640, cx: 600, cy: 470, rx: 470, ry: 330, coreR: 112 };

const state = { mode: "sample", runs: [], meta: {}, histAct: 0, runId: "sample", vm: null, poll: null, replaying: false, stageIdx: 6, panel: null, panelStack: [], feed: [], seen: {}, cseen: {} };

/* ---------------- helpers ---------------- */
const advName = (id, short) => { const a = ADVISERS[id]; return a ? (short && a.short) || a.name : (id || "Adviser"); };
const last = a => Array.isArray(a) && a.length ? a[a.length - 1] : null;
const upper = s => String(s || "").toUpperCase();
function stanceKey(s) { s = String(s || "").toLowerCase(); if (s.includes("_b") || s === "b") return "b"; if (s.includes("_a") || s === "a") return "a"; if (s.includes("cond")) return "c"; return "n"; }
const stanceLabel = k => k === "a" ? "Backs option A" : k === "b" ? "Backs option B" : k === "c" ? "Conditional" : "Not enough evidence";
const stanceShort = k => k === "a" ? "backs A" : k === "b" ? "backs B" : k === "c" ? "conditional" : "unsure";
const stanceVar = k => k === "a" ? "var(--opt-a)" : k === "b" ? "var(--opt-b)" : k === "c" ? "var(--cond)" : "var(--ink-3)";
function fmtDur(s) { if (s == null || isNaN(s)) return ""; s = Math.round(s); if (s < 60) return s + "s"; return Math.floor(s / 60) + "m " + String(s % 60).padStart(2, "0") + "s"; }
function fmtClock(s) { s = Math.max(0, Math.round(s || 0)); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); }
function fmtNum(v, unit) {
  if (v == null || v === "") return "";
  const n = Number(v); const t = isNaN(n) ? v : n.toLocaleString("en-IN", { maximumFractionDigits: 2 });
  unit = unit || "";
  if (unit.startsWith("₹ ")) return "₹" + t + " " + unit.slice(2);
  if (unit === "%") return t + "%";
  return (t + " " + unit).trim();
}
function fmtTime(iso) { if (!iso) return ""; const d = new Date(iso); return isNaN(d) ? String(iso) : d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); }
function chip(id) {
  const f = FACTS[id] || {}; const c = ["chip"]; if (f.superseded_by) c.push("old"); else if (f.reliability === "low") c.push("low");
  return `<button type="button" class="${c.join(" ")}" data-ev="${esc(id)}">${esc(id)}</button>`;
}
const EVID_G = /\b(?:FIN|BUS|CUS|LEG|SEC|OPS)-\d{2,3}\b|\bBRIEF\b/g;
const EVID_T = /\b(?:FIN|BUS|CUS|LEG|SEC|OPS)-\d{2,3}\b|\bBRIEF\b/;
function withChips(text) {
  const raw = String(text || ""); const ids = [...new Set(raw.match(EVID_G) || [])]; let by = "";
  const clean = raw.replace(/\s*\(([^)]*)\)\s*\.?\s*$/, (m, inner) => {
    if (!EVID_T.test(inner)) return m;
    by = inner.replace(EVID_G, "").replace(/^[\s,;]+|[\s,;]+$/g, "").replace(/\s*[;,]\s*[;,]\s*/g, ", ");
    return m.trim().endsWith(".") ? "." : "";
  });
  return { text: clean, ids, by };
}
function glyph(kind, cls = "g") {
  switch (kind) {
    case "rupee": return `<text class="gt" y="9">₹</text>`;
    case "bars": return `<path class="${cls}" d="M-11 10V3M-4 10V-3M3 10V-9M10 10V-1"/>`;
    case "person": return `<g class="${cls}"><circle cx="0" cy="-6" r="6"/><path d="M-12 12c2-7 6-10 12-10s10 3 12 10"/></g>`;
    case "scales": return `<g class="${cls}"><path d="M0-12V11M-8 11h16M-12-7h24"/><path d="M-12-7l-4 9h8zM12-7l-4 9h8z"/></g>`;
    case "shield": return `<path class="${cls}" d="M0-13l11 4.5v8c0 7.5-5.5 12-11 14-5.5-2-11-6.5-11-14v-8z"/>`;
    case "gear": return `<g class="${cls}"><circle r="5.5"/><circle r="11" stroke-dasharray="4.4 3.5"/></g>`;
  }
  return "";
}
function avatar(id) {
  const g = glyph((ADVISERS[id] || {}).glyph).replace('class="gt" y="9"', 'y="8" text-anchor="middle" style="fill:var(--ink);font:600 19px var(--f-display)"')
    .replace(/class="g"/g, 'fill="none" stroke="var(--ink)" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"');
  return `<svg viewBox="-17 -17 34 34">${g}</svg>`;
}

/* ---------------- adapter ---------------- */
function toViewModel(run) {
  run = run || {};
  const brief = run.brief || {};
  const routing = run.routing || {};
  let positions = run.positions || [];
  if (!Array.isArray(positions)) positions = Object.entries(positions).map(([k, v]) => ({ adviser_id: k, ...v }));
  if (!positions.length && run.positions_live && typeof run.positions_live === "object")
    positions = Object.entries(run.positions_live).map(([k, v]) => ({ adviser_id: k, ...v }));
  const advStatus = {};
  Object.entries(run.adviser_status || {}).forEach(([k, v]) => { advStatus[k] = typeof v === "string" ? { state: v.toLowerCase() } : { ...v, state: String((v && v.state) || "").toLowerCase() }; });
  const verdicts = {};
  const mod = run.moderation || run.moderator || run.moderator_result || run.moderation_result || {};
  const vl = run.claim_verdicts || run.verdicts || mod.claim_verdicts || mod.verdicts || [];
  const normStatus = x => { let t = upper(x).replace(/^CLAIMSTATUS\./, ""); return ({ STRUCK: "STRIKE", FLAGGED: "FLAG", KEPT: "KEEP", OK: "KEEP", PASS: "KEEP" })[t] || t; };
  (Array.isArray(vl) ? vl.map(v => [null, v]) : Object.entries(vl)).forEach(([key, v]) => {
    if (v == null) return; if (typeof v === "string") v = { status: v };
    const id = v.claim_id || v.id || key; if (!id) return;
    verdicts[id] = { ...v, claim_id: id, status: normStatus(v.status ?? v.verdict ?? v.decision ?? v.result), reason: v.reason || v.detail || v.explanation || "" };
  });
  const memoRevs = (run.memo_revisions || run.memo_versions || []).map(m => (m && m.memo) ? m.memo : m).filter(Boolean);
  const memo = run.memo || last(memoRevs);
  let finals = run.final_verdicts || run.verdict_history || [];
  if (!finals.length && Array.isArray(run.memo_revisions)) finals = run.memo_revisions.map(m => m && m.verdict).filter(Boolean);
  const final = run.final_verdict || last(finals);
  if (final && !finals.length) finals = [final];
  const scorecard = run.scorecard || (memo && memo.scorecard) || null;
  let decisions = run.human_decisions || (run.human_decision ? [run.human_decision] : []);
  if (!Array.isArray(decisions)) decisions = Object.values(decisions);
  const scores = (routing.scores || []).map(s => ({ id: s.adviser_id, sim: Number(s.similarity ?? s.score ?? 0), selected: !!s.selected, reason: s.reason || "", cap: s.best_matching_capability || "" }));
  const selected = routing.selected || scores.filter(s => s.selected).map(s => s.id);
  let status = String(run.status || "").toLowerCase();
  if (!status) status = final ? "done" : (memo ? "checking" : positions.length ? "moderating" : "routing");
  const stageIdx = status === "idle" ? -1 : status === "done" ? 6 : status === "failed" ? -2 : Math.max(0, STAGES.findIndex(s => s.key === status));
  positions = positions.map(p => ({ ...p, claims: (p.claims || []).map(c => (c.claim_id || !c.id) ? c : { ...c, claim_id: c.id }) }));
  const posById = {}; positions.forEach(p => posById[p.adviser_id] = p);
  const blocking = positions.filter(p => (p.blocking_findings || []).length).map(p => p.adviser_id);
  return { run, id: run.run_id, brief, opts: brief.options || {}, routing, scores, selected, positions, posById, verdicts, memo, memoRevs,
           finals, final, scorecard, decisions, status, stageIdx, blocking, advStatus, metrics: run.metrics || {}, created: run.created_at, error: run.error };
}
const IDLE_RUN = { status: "idle", brief: { decision_id: "", question: "", options: {}, constraints: [] } };
function claimStats(vm, id) {
  const p = vm.posById[id]; let k = 0, f = 0, s = 0;
  (p ? p.claims || [] : []).forEach(c => { const st = upper((vm.verdicts[c.claim_id] || {}).status) || "KEEP"; if (st === "STRIKE") s++; else if (st === "FLAG") f++; else k++; });
  return { k, f, s };
}
function headline(p) {
  if (!p) return "";
  const c = (p.claims || []).find(c => c.kind === "calculation" && c.value != null) || (p.claims || [])[0];
  let t = c ? c.text : p.summary; if (t && t.length > 110) t = t.slice(0, 106).replace(/\s+\S*$/, "") + "…"; return t || "";
}

/* ---------------- arena ---------------- */
function seatPos(i, n) {
  const a0 = 168, a1 = 12; const ang = (a0 - (a0 - a1) * (n === 1 ? .5 : i / (n - 1))) * Math.PI / 180;
  return { x: GEO.cx + GEO.rx * Math.cos(ang), y: GEO.cy - GEO.ry * Math.sin(ang) };
}
function arcPath(r, a0, a1) {
  const p = a => [GEO.cx + r * Math.cos(a * Math.PI / 180), GEO.cy + r * Math.sin(a * Math.PI / 180)];
  const [x0, y0] = p(a0), [x1, y1] = p(a1);
  return `M${x0.toFixed(1)} ${y0.toFixed(1)} A${r} ${r} 0 ${(a1 - a0) > 180 ? 1 : 0} 1 ${x1.toFixed(1)} ${y1.toFixed(1)}`;
}
function renderArena(vm) {
  const { cx, cy, coreR } = GEO;
  const ids = SEAT_ORDER.filter(id => ADVISERS[id]); vm.scores.forEach(s => { if (!ids.includes(s.id)) ids.push(s.id); });
  const seats = ids.map((id, i) => ({ id, i, ...seatPos(i, ids.length) }));
  state.seats = seats;
  let h = `<defs>
    <radialGradient id="spotG"><stop offset="0" stop-color="currentColor" stop-opacity=".9"/><stop offset="1" stop-color="currentColor" stop-opacity="0"/></radialGradient>
    <radialGradient id="haloGrad"><stop offset=".55" stop-color="currentColor" stop-opacity=".0"/><stop offset=".7" stop-color="currentColor" stop-opacity=".5"/><stop offset="1" stop-color="currentColor" stop-opacity="0"/></radialGradient>
    <radialGradient id="seatFill" cx=".35" cy=".3"><stop offset="0" stop-color="#21414B"/><stop offset="1" stop-color="#0D1F25"/></radialGradient>
    <radialGradient id="coreFill" cx=".5" cy=".35"><stop offset="0" stop-color="#173740"/><stop offset="1" stop-color="#08161B"/></radialGradient>
    <linearGradient id="beamGrad" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#4FD8FF" stop-opacity=".9"/><stop offset="1" stop-color="#4FD8FF" stop-opacity="0"/></linearGradient>
  </defs>`;
  h += `<ellipse class="spot" cx="${cx}" cy="${cy}" rx="420" ry="230" fill="url(#spotG)"/>`;
  [[300, 190, ""], [395, 262, "dash"], [500, 350, ""], [600, 430, "dash"]].forEach(([rx, ry, c]) => { h += `<path class="tier ${c}" d="M${cx - rx} ${cy + 60} A${rx} ${ry} 0 0 1 ${cx + rx} ${cy + 60}"/>`; });
  // links + packets
  seats.forEach(s => {
    const sel = vm.selected.includes(s.id); const pos = vm.posById[s.id];
    const ang = Math.atan2(s.y - cy, s.x - cx); const ex = cx + (coreR + 26) * Math.cos(ang), ey = cy + (coreR + 26) * Math.sin(ang);
    const mx = (s.x + ex) / 2 + (cx - (s.x + ex) / 2) * .12, my = (s.y + ey) / 2 - 30;
    const d = `M${s.x.toFixed(1)} ${s.y.toFixed(1)} Q${mx.toFixed(1)} ${my.toFixed(1)} ${ex.toFixed(1)} ${ey.toFixed(1)}`;
    const k = pos ? stanceKey(pos.stance) : "n";
    h += `<path id="ln-${s.id}" class="link ${sel ? "on" : ""}" style="--i:${s.i};--sc:${stanceVar(k)}" d="${d}"/>`;
    h += `<circle class="packet ${sel ? "on" : ""}" r="4"><animateMotion dur="${(1.4 + s.i * .17).toFixed(2)}s" repeatCount="indefinite" path="${d}"/></circle>`;
  });
  // the core: pipeline ring + disc
  const R = coreR + 16, n = STAGES.length, gap = 4, span = 360 / n;
  h += `<circle class="core-halo" cx="${cx}" cy="${cy}" r="${coreR}" style="--r0:${coreR}px;--r1:${coreR + 46}px"/><circle class="core-halo" cx="${cx}" cy="${cy}" r="${coreR}" style="--r0:${coreR}px;--r1:${coreR + 46}px;animation-delay:1.4s"/>`;
  h += `<g class="core" id="core" tabindex="0" role="button" aria-label="Open the verdict">`;
  h += `<circle class="burst" cx="${cx}" cy="${cy}" r="${R}"/><circle class="burst b2" cx="${cx}" cy="${cy}" r="${R}"/>`;
  h += `<path class="wreath-line" d="${arcPath(R + 22, 100, 250)}"/><path class="wreath" d="${arcPath(R + 22, 100, 250)}"/>`;
  h += `<path class="wreath-line" d="${arcPath(R + 22, 290, 440)}"/><path class="wreath" d="${arcPath(R + 22, 290, 440)}"/>`;
  STAGES.forEach((s, i) => { h += `<path class="seg" data-i="${i}" d="${arcPath(R, -90 + i * span + gap / 2, -90 + (i + 1) * span - gap / 2)}"/>`; });
  h += `<line class="beam" x1="${cx}" y1="${cy}" x2="${cx}" y2="${cy - 420}" style="transform-origin:${cx}px ${cy}px"/>`;
  h += `<circle class="core-disc" cx="${cx}" cy="${cy}" r="${coreR}"/>`;
  h += `<text class="core-id" id="coreId" x="${cx}" y="${cy - 38}"></text><text class="core-word" id="coreWord" x="${cx}" y="${cy + 8}"></text>`;
  h += `<text class="core-sub" id="coreSub1" x="${cx}" y="${cy + 36}"></text><text class="core-sub" id="coreSub2" x="${cx}" y="${cy + 54}"></text><text class="core-cta" x="${cx}" y="${cy + 84}">CLICK TO BEGIN</text></g>`;
  // seats
  const C = 2 * Math.PI * 58;
  seats.forEach(s => {
    const sc = vm.scores.find(x => x.id === s.id); const sim = sc ? sc.sim : 0; const pos = vm.posById[s.id];
    const ast = (vm.advStatus[s.id] || {}).state; const failed = ["failed", "timeout", "error"].includes(ast);
    const c = ["seat"]; if (vm.selected.includes(s.id)) c.push("sel"); if (pos) c.push("has-pos", "stance-" + stanceKey(pos.stance));
    if (vm.blocking.includes(s.id)) c.push("blocking"); if (failed) c.push("failed");
    if (pos && vm.status === "advisers") { c.push("arrived"); const seen = seenFor(vm.id); if (!seen.has(s.id)) { seen.add(s.id); if (seen.primed) { c.push("pop"); (state.newArrivals = state.newArrivals || []).push(s.id); } } }
    const st = claimStats(vm, s.id);
    const sub = failed ? (ast === "timeout" ? "timed out" : "failed") : !sc ? (vm.status === "idle" ? "waiting" : "") : (pos && vm.stageIdx >= 2 ? stanceShort(stanceKey(pos.stance)) + "  " : "") + sim.toFixed(2);
    h += `<g class="${c.join(" ")}" style="--i:${s.i}" data-adv="${esc(s.id)}" transform="translate(${s.x.toFixed(1)} ${s.y.toFixed(1)})" tabindex="0" role="button" aria-label="${esc(advName(s.id))}">
      <circle class="halo" r="92"/>
      <circle class="score" r="58" stroke-dasharray="${C.toFixed(1)}" stroke-dashoffset="${(C * (1 - Math.max(0, Math.min(1, sim)))).toFixed(1)}" transform="rotate(-90)"/>
      <circle class="spin" r="58"/>
      <circle class="ring" r="48"/><circle class="body" r="44"/><circle class="tint" r="44"/>
      ${glyph((ADVISERS[s.id] || {}).glyph)}
      <g transform="translate(36 34)"><g class="badge ok"><circle r="12"/><path d="M-5.5 .5l3.5 3.5 7-7.5"/></g></g>
      <g transform="translate(36 34)"><g class="badge xm"><circle r="12"/><path d="M-4.5-4.5l9 9M4.5-4.5l-9 9" stroke-width="2.6" stroke-linecap="round"/></g></g>
      <g transform="translate(37 -36)"><g class="badge shield"><path d="M0-13l11 4.5v7.5c0 7.5-5.5 12-11 14-5.5-2-11-6.5-11-14v-7.5z"/><text y="5">!</text></g></g>
      <text class="nm" y="80">${esc(advName(s.id, true))}</text>
      <text class="sb" y="98">${esc(sub)}</text>
      ${(st.s || st.f) ? `<g class="tally-pill" transform="translate(0 118)">${st.s ? `<g transform="translate(${st.f ? -34 : 0} 0)"><rect x="-32" y="-11" width="64" height="20" style="stroke:var(--strike)" stroke-width="1"/><text y="3" style="fill:var(--strike)">${st.s} struck</text></g>` : ""}${st.f ? `<g transform="translate(${st.s ? 34 : 0} 0)"><rect x="-32" y="-11" width="64" height="20" style="stroke:var(--flag)" stroke-width="1"/><text y="3" style="fill:var(--flag)">${st.f} flagged</text></g>` : ""}</g>` : ""}
    </g>`;
  });
  const svg = $("#arena"); svg.innerHTML = h;
  const seen = seenFor(vm.id); if (!seen.primed) seen.primed = true;
  $$(".seat", svg).forEach(g => {
    const id = g.dataset.adv;
    g.addEventListener("click", () => openAdviser(id));
    g.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openAdviser(id); } });
    g.addEventListener("mouseenter", () => hoverSeat(id, true));
    g.addEventListener("mouseleave", () => hoverSeat(id, false));
  });
  const core = $("#core");
  core.addEventListener("click", () => openVerdict());
  core.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openVerdict(); } });
  if (state.newArrivals && state.newArrivals.length) {
    const ids2 = state.newArrivals; state.newArrivals = [];
    ids2.forEach(id => { pulseTo(id); bubble(id, headline(vm.posById[id]), advName(id, true) + " made the case", "", 3200, "arrive"); feed(`<span class="e-sig">${esc(advName(id, true))}</span> made the case`, "ok"); });
    toast(ids2.map(a => advName(a, true)).join(", ") + (ids2.length > 1 ? " have" : " has") + " made the case.");
  }
}
function seenFor(id) { return state.seen[id || "_"] = state.seen[id || "_"] || new Set(); }

function pulseTo(id) {
  const path = $(`#ln-${CSS.escape(id)}`); if (!path || reduced) return;
  const p = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  p.setAttribute("r", "7"); p.setAttribute("class", "packet go"); p.style.setProperty("--sc", path.style.getPropertyValue("--sc"));
  const m = document.createElementNS("http://www.w3.org/2000/svg", "animateMotion");
  m.setAttribute("dur", ".9s"); m.setAttribute("fill", "freeze"); m.setAttribute("path", path.getAttribute("d"));
  p.appendChild(m); $("#arena").appendChild(p); try { m.beginElement(); } catch (e) {}
  setTimeout(() => p.remove(), 1000);
}
function bubble(id, text, title, kind = "", ms = 2800, group = "") {
  if (!text) return;
  if (group) $$(`#bubbles .bub[data-g="${group}"]`).forEach(x => { x.classList.add("out"); setTimeout(() => x.remove(), 350); });
  const s = (state.seats || []).find(x => x.id === id); if (!s) return;
  const b = document.createElement("div");
  b.className = "bub " + kind + (s.y < 330 ? " low" : "");
  const pos = state.vm && state.vm.posById[id]; if (pos) b.style.setProperty("--sc", stanceVar(stanceKey(pos.stance)));
  b.style.left = (s.x / GEO.W * 100) + "%"; b.style.top = (s.y / GEO.H * 100) + "%";
  b.innerHTML = `<b>${esc(title)}</b>${esc(text)}`; if (group) b.dataset.g = group;
  $("#bubbles").appendChild(b);
  setTimeout(() => { b.classList.add("out"); setTimeout(() => b.remove(), 400); }, ms);
}
let hoverBub = null;
function hoverSeat(id, on) {
  $$(`[data-adv="${CSS.escape(id)}"]`).forEach(el => el.classList.toggle("hl", on));
  if (hoverBub) { hoverBub.remove(); hoverBub = null; }
  if (!on || !state.vm || state.replaying) return;
  const vm = state.vm, sc = vm.scores.find(x => x.id === id), p = vm.posById[id];
  const s = (state.seats || []).find(x => x.id === id); if (!s) return;
  const b = document.createElement("div"); b.className = "bub";
  if (p) b.style.setProperty("--sc", stanceVar(stanceKey(p.stance)));
  b.style.left = (s.x / GEO.W * 100) + "%"; b.style.top = (s.y / GEO.H * 100) + "%";
  b.innerHTML = `<b>${esc(advName(id))}${p ? ": " + esc(stanceLabel(stanceKey(p.stance)).toLowerCase()) : ""}</b>${sc ? `Routing ${sc.sim.toFixed(2)}, ${esc(sc.reason || (sc.selected ? "selected" : "not selected"))}. ` : ""}${p ? esc(headline(p)) : ""}`;
  $("#bubbles").appendChild(b); hoverBub = b;
}

/* ---------------- session state: idle / live / done / failed ---------------- */
function arenaKind(vm) { if (!vm || vm.status === "idle") return "idle"; if (vm.status === "done") return "done"; if (vm.status === "failed") return "failed"; return "live"; }
/* verdict codes stay PASS / INCOMPLETE / WRONG in the data; people see these labels */
const VLAB = { PASS: "PASS", INCOMPLETE: "NEEDS EVIDENCE", WRONG: "HELD BACK" };
const VSENT = { PASS: "Pass", INCOMPLETE: "Needs evidence", WRONG: "Held back" };
const vlab = v => VLAB[v] || v;
const vsent = v => VSENT[v] || v;
function arrivedCount(vm) { if (state.replaying && state.replayArrived != null) return state.replayArrived; return vm.positions.filter(p => vm.selected.includes(p.adviser_id)).length; }
function setSession(kind, stageIdx) {
  const vm = state.vm, app = $("#app");
  app.classList.remove("idle", "live", "done", "failed", "v-pass", "v-incomplete", "v-wrong");
  app.classList.add(kind);
  const verdict = upper(vm && vm.final && vm.final.verdict) || "PASS";
  if (kind === "done") app.classList.add("v-" + verdict.toLowerCase());
  let st = "", dt = "", word = "", s1 = "", s2 = "";
  if (kind === "idle") { st = "Arena idle"; dt = "No debate in session"; word = "IDLE"; s1 = "Start a new decision"; s2 = "or open a saved run"; }
  else if (kind === "live") {
    let t = STAGES[Math.max(0, Math.min(5, stageIdx))].live;
    if (stageIdx === 1 && vm) t = `${arrivedCount(vm)} of ${vm.selected.length || 6} advisers have made their case`;
    st = "In session"; dt = t; word = "DEBATING"; s1 = STAGES[Math.max(0, Math.min(5, stageIdx))].label; s2 = stageIdx === 1 && vm ? `${arrivedCount(vm)} of ${vm.selected.length || 6} in` : "";
  } else if (kind === "failed") { const sg = String((vm && (vm.run.failed_stage || vm.run.stage)) || "").toLowerCase(); st = "Run stopped"; dt = sg ? "Stopped at " + sg : "See the timeline"; word = "STOPPED"; s1 = sg || ""; }
  else {
    const k = stanceKey(vm && vm.memo && vm.memo.stance);
    st = "Session closed"; dt = `Verdict: ${vsent(verdict)}. Ready for sign-off.`; word = vlab(verdict);
    s1 = k === "c" ? "Conditional move" : k === "b" ? "Recommends B" : k === "a" ? "Recommends A" : "Recommendation ready";
    s2 = k === "c" ? "to option B, in phases" : "Click to read the verdict";
  }
  $("#sessState").textContent = st; $("#sessDetail").textContent = dt;
  const coreEl = $("#core"); if (coreEl) coreEl.setAttribute("aria-label", kind === "idle" ? "Start a new decision or open a saved run" : kind === "done" ? "Open the verdict" : "Open the session log");
  const w = $("#coreWord"); if (w) { $("#coreId").textContent = (vm && vm.brief.decision_id) || ""; w.textContent = word; w.style.fontSize = word.length > 10 ? "17px" : word.length > 8 ? "23px" : ""; $("#coreSub1").textContent = s1; $("#coreSub2").textContent = s2; }
}
function setSegments(idx, failed) {
  $$("#arena .seg").forEach(seg => {
    const i = Number(seg.dataset.i); seg.classList.remove("done", "now", "fail");
    if (failed) { if (i < idx) seg.classList.add("done"); else if (i === idx) seg.classList.add("fail"); }
    else if (idx === 6 || i < idx) seg.classList.add("done"); else if (i === idx) seg.classList.add("now");
  });
}
function sweepPath() {
  if (reduced) return; state.sweep = true; renderTimeline();
  setTimeout(() => { state.sweep = false; $$("#path .step").forEach(b => b.classList.remove("sweep-on")); }, 2600);
}
function seenCoach() { try { return localStorage.getItem("colosseum-coach") === "1"; } catch (e) { return false; } }
function showCoach() {
  if (seenCoach() || $(".coach") || !state.vm || state.vm.status !== "done") return;
  const target = $('#path .step[data-sheet="debate"]'); if (!target) return;
  state.coachOn = true; renderTimeline();
  const t = $('#path .step[data-sheet="debate"]').getBoundingClientRect();
  const c = document.createElement("div"); c.className = "coach"; c.setAttribute("role", "dialog"); c.setAttribute("aria-label", "Tip");
  c.innerHTML = `<b>Every step opens up</b><span>The bar below is the decision path. Click <strong style="font-weight:500">Their arguments</strong> to see each adviser's case, or click the verdict in the middle of the arena.</span><div class="row-actions"><button class="btn" type="button" id="coachOk">Got it</button><button class="btn primary" type="button" id="coachGo">Show me</button></div>`;
  document.body.appendChild(c);
  const w = c.getBoundingClientRect().width;
  const left = Math.max(16, Math.min(window.innerWidth - w - 16, t.left + t.width / 2 - 40));
  c.style.left = left + "px"; c.style.top = (t.top - c.getBoundingClientRect().height - 14) + "px";
  c.style.setProperty("--ax", Math.max(16, t.left + t.width / 2 - left - 7) + "px");
  const done = () => { try { localStorage.setItem("colosseum-coach", "1"); } catch (e) {} state.coachOn = false; c.remove(); renderTimeline(); };
  $("#coachOk").addEventListener("click", done);
  $("#coachGo").addEventListener("click", () => { done(); openSheet("debate"); });
  document.addEventListener("click", function once(e) { if (!c.contains(e.target) && e.target.closest("[data-sheet], .seat, #core")) { done(); document.removeEventListener("click", once, true); } }, true);
}
function burst() { const a = $("#app"); a.classList.remove("bursting"); void a.offsetWidth; a.classList.add("bursting"); setTimeout(() => a.classList.remove("bursting"), 1800); }

function applyStage(idx, working) {
  state.stageIdx = idx;
  const app = $("#app");
  for (let i = 0; i <= 6; i++) app.classList.toggle("s" + i, idx >= i);
  app.classList.toggle("working", !!working);
  setSegments(idx, false);
  renderTimeline();
  const idle = !state.vm || state.vm.status === "idle";
  $("#replayBtn").disabled = idle || state.replaying;
  $("#briefLink").hidden = idle;
}
function stepHint(i, vm) {
  if (!vm) return "";
  const tot = (vm.scorecard && vm.scorecard.weighted_totals) || {};
  switch (STAGES[i].key) {
    case "routing": return `${vm.selected.length} advisers joined`;
    case "advisers": return `${vm.positions.length} positions argued`;
    case "moderating": { let f = 0, x = 0; vm.positions.forEach(p => { const t = claimStats(vm, p.adviser_id); f += t.f; x += t.s; }); return x || f ? `${x} struck, ${f} flagged` : "All claims held up"; }
    case "scoring": { const o = Object.entries(tot).sort((a, b) => b[1] - a[1]); return o.length > 1 ? `${o[0][0]} leads ${Number(o[0][1]).toFixed(2)} to ${Number(o[1][1]).toFixed(2)}` : "Scores ready"; }
    case "writing": { const k = stanceKey(vm.memo && vm.memo.stance); return k === "c" ? "Conditional, in phases" : k === "b" ? "Go with option B" : k === "a" ? "Go with option A" : "Memo ready"; }
    case "checking": { const v = upper(vm.final && vm.final.verdict); const r = Math.max(0, vm.finals.length - 1); return v ? `${vsent(v)}${r ? ` after ${r} revision${r > 1 ? "s" : ""}` : " first time"}` : "Checked"; }
    case "done": { const d = last(vm.decisions); return d ? `${String(d.action).toLowerCase() === "override" ? "Overridden" : "Approved"} by ${String(d.decided_by || "").split(",")[0]}` : "Waiting for you"; }
  }
  return "";
}
function renderTimeline() {
  const vm = state.vm, idx = state.stageIdx, failed = vm && vm.status === "failed";
  const idle = !vm || vm.status === "idle";
  const failIdx = failed ? Math.max(0, STAGES.findIndex(s => s.key === String(vm.run.failed_stage || vm.run.stage || "").toLowerCase())) : -1;
  const streaming = vm && vm.status === "advisers" && vm.positions.length && !state.replaying;
  $("#path").innerHTML = `<span class="fill" id="pathFill"></span>` + STAGES.map((s, i) => {
    let c = ""; if (failed) c = i < failIdx ? "done" : i === failIdx ? "fail" : ""; else c = (idx === 6 || i < idx) ? "done" : i === idx ? "now" : "";
    if (s.key === "done" && c === "now") c = "done";
    let hint = idle ? "Not started" : c === "done" ? stepHint(i, vm) : c === "now" ? (s.key === "advisers" ? `${arrivedCount(vm)} of ${vm.selected.length || 6} have argued` : "In progress") : c === "fail" ? "Stopped here" : "Not started";
    const open = !idle && (c === "done" || (s.key === "advisers" && (streaming || (c === "now" && arrivedCount(vm) > 0 && !state.replaying))));
    const t = vm ? vm.metrics[s.key + "_s"] : null;
    return `<button type="button" style="--k:${i}" class="step ${c} ${s.key === "done" ? "you" : ""} ${state.sheet === s.sheet ? "on" : ""} ${state.sweep ? "sweep-on" : ""} ${state.coachOn && s.key === "advisers" ? "d-coach" : ""}" data-sheet="${s.sheet}" ${open ? "" : "disabled"}
      title="${open ? "Open: " + esc(s.label) : esc(s.label) + ": not ready yet"}${t != null && c === "done" ? " (took " + fmtDur(t) + ")" : ""}">
      <span class="d"></span><span class="n">${esc(s.label)}</span><span class="h">${esc(hint)}</span>
      <svg class="go" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="M7 17L17 7M9 7h8v8"/></svg></button>`;
  }).join("");
  $("#pathCap").innerHTML = idle ? "Start a new decision or open a saved run to fill in the path." :
    `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M7 17L17 7M9 7h8v8"/></svg>` + (vm.status === "done" ? "Each step opens its detail. Click one to see who argued what, what was struck, and why." : "Steps light up as the run moves. Finished steps open their detail.");
  const reach = failed ? failIdx : idx;
  const fill = $("#pathFill"); const steps = $$("#path .step");
  if (fill && steps.length && reach > 0) { const a = steps[0].getBoundingClientRect(), b = steps[Math.min(reach, 6)].getBoundingClientRect(), p = $("#path").getBoundingClientRect();
    fill.style.width = Math.max(0, (b.left + 18) - (a.left + 18)) + "px"; fill.style.left = (a.left - p.left + 20) + "px"; }
  else if (fill) fill.style.width = "0";
}

/* ---------------- top + title ---------------- */
function renderTitle(vm) {
  const idle = vm.status === "idle", b = vm.brief;
  $("#question").textContent = idle ? "Bring a decision to the arena." : (b.question || "Untitled decision");
  $("#opts").innerHTML = idle ? `<span class="muted">Six advisers argue it out. A moderator checks every claim. A named person decides.</span>` :
    Object.entries(vm.opts).map(([k, v]) => `<span class="${k.toLowerCase()}"><i>${esc(k)}</i>${esc(v)}</span>`).join("");
  $("#crumbId").textContent = idle ? "" : (b.decision_id || "");
  $("#crumbT").textContent = idle ? "AI debate room for enterprise decisions" : (state.runId === "sample" || !vm.created) ? "Sample run" : (vm.status === "done" || vm.status === "failed" ? "Decided " : "Started ") + fmtTime(vm.created);
}

function renderAll(vm) {
  const safe = (n, fn) => { try { fn(vm); } catch (e) { console.error("[colosseum] " + n, e); } };
  safe("title", renderTitle); safe("arena", renderArena);
  const idx = vm.status === "failed" ? (vm.positions.length ? 2 : 1) : vm.stageIdx;
  applyStage(idx, vm.status === "advisers");
  if (vm.status === "failed") setSegments(Math.max(0, STAGES.findIndex(s => s.key === String(vm.run.failed_stage || vm.run.stage || "").toLowerCase())), true);
  updateClock();
  if (!state.replaying) setSession(arenaKind(vm), vm.stageIdx);
  if (state.panel) refreshPanel(true);
  if (state.sheet) openSheet(state.sheet, true);
}

/* ---------------- clock ---------------- */
let clockTimer = null;
function updateClock() {
  const vm = state.vm; if (!vm) return;
  let s = vm.metrics.latency_s;
  if (!["done", "failed", "idle"].includes(vm.status) && vm.created) s = (Date.now() - new Date(vm.created).getTime()) / 1000;
  $("#clock").textContent = vm.status === "idle" ? "0:00" : fmtClock(s || 0);
}

/* ---------------- side panel ---------------- */
function openPanel(kind, arg, push) {
  if (push && state.panel) state.panelStack.push(state.panel); else if (!push) state.panelStack = [];
  state.panel = { kind, arg };
  $("#side").classList.add("open"); $("#app").classList.add("panel-open");
  refreshPanel();
}
function closePanel() { state.panel = null; state.panelStack = []; $("#side").classList.remove("open"); $("#app").classList.remove("panel-open"); $$(".seat.focus").forEach(s => s.classList.remove("focus")); }
function backPanel() { const p = state.panelStack.pop(); if (p) { state.panel = p; refreshPanel(); } else closePanel(); }
function refreshPanel(keepScroll) {
  const p = state.panel; if (!p) return;
  if (!keepScroll) $("#sideBody").scrollTop = 0;
  $("#sideBack").hidden = !state.panelStack.length;
  $$(".seat.focus").forEach(s => s.classList.remove("focus"));
  if (p.kind === "adviser") { $(`#arena .seat[data-adv="${CSS.escape(p.arg)}"]`)?.classList.add("focus"); panelAdviser(p.arg); }
  else if (p.kind === "verdict") panelVerdict();
  else if (p.kind === "feed") panelFeed();
  else if (p.kind === "evidence") panelEvidence(p.arg);
  else if (p.kind === "start") panelStart();
}
function openAdviser(id) { if (!state.vm || !state.vm.posById[id] && !state.vm.scores.find(s => s.id === id)) return; openPanel("adviser", id); }
function openVerdict() { if (!state.vm || state.vm.status === "idle") { openPanel("start"); return; } if (state.vm.status !== "done") { openPanel("feed"); return; } openPanel("verdict"); }

function panelAdviser(id) {
  const vm = state.vm, p = vm.posById[id], sc = vm.scores.find(s => s.id === id);
  $("#sideTitle").textContent = "Adviser";
  const k = p ? stanceKey(p.stance) : "n";
  const st = claimStats(vm, id);
  const showAll = state.showAllClaims === id;
  let body = `<div class="ph" style="--sc:${stanceVar(k)}"><div class="av">${avatar(id)}</div><div><h3>${esc(advName(id))}</h3>
    <div style="margin-top:6px">${p ? `<span class="pill sp-${k}">${esc(stanceLabel(k))}</span>` : `<span class="pill sp-n">${vm.status === "advisers" ? "Still arguing" : "No position"}</span>`}</div></div></div>`;
  if (sc) body += `<div class="kv"><span>Routing <b>${sc.sim.toFixed(2)}</b></span>${p ? `<span>Confidence <b>${Math.round((p.confidence || 0) * 100)}%</b></span>` : ""}<span>${esc(sc.reason || "")}</span></div>`;
  if (!p) { $("#sideBody").innerHTML = body + `<p class="lede">${vm.status === "advisers" ? "This adviser is still building its case. Its seat ticks the moment it finishes." : "This adviser wasn't selected for this decision."}</p>`; return; }
  body += `<p class="lede">${esc(p.summary)}</p>`;
  (p.blocking_findings || []).forEach((b, i) => {
    const bv = vm.verdicts[`${id}-B${i + 1}`];
    body += `<div class="blk"><h4>Blocking finding</h4><div>${esc(b.text)}</div><div class="fix"><b style="color:var(--ink);font-weight:500">Fix:</b> ${esc(b.fix)}</div><div class="chips">${(b.evidence_ids || []).map(chip).join("")}</div>${bv ? `<div class="muted" style="font-size:12.5px">Moderator: ${esc(bv.reason)}</div>` : ""}</div>`;
  });
  const claims = (p.claims || []).map(c => ({ c, v: vm.verdicts[c.claim_id] }));
  claims.sort((a, b) => rank(b.v) - rank(a.v));
  const shown = showAll ? claims : claims.slice(0, 4);
  body += `<div class="h4">Claims <span class="tally" style="display:inline-flex;margin-left:8px"><span class="k">${st.k} kept</span><span class="f">${st.f} flagged</span><span class="s">${st.s} struck</span></span></div>`;
  body += `<ul class="claims">${shown.map(({ c, v }) => { const s = upper(v && v.status) || "KEEP"; const cls = s === "STRIKE" ? "strike" : s === "FLAG" ? "flag" : "keep";
    return `<li class="cl ${cls}"><span class="st">${cls === "strike" ? "✕" : cls === "flag" ? "!" : "✓"}</span><div><span class="tx">${esc(c.text)}</span>${c.formula ? `<span class="calc">${esc(c.formula)}${c.value != null ? " = " + esc(fmtNum(c.value, c.unit)) : ""}</span>` : ""}${v && cls !== "keep" ? `<div class="why">${esc(v.reason)}</div>` : ""}<div class="chips">${(c.evidence_ids || []).map(chip).join("")}</div></div></li>`; }).join("")}</ul>`;
  if (claims.length > 4) body += `<button class="more" type="button" id="moreClaims">${showAll ? "Show fewer claims" : `Show all ${claims.length} claims`}</button>`;
  const crit = {}; (p.criterion_scores || []).forEach(s => { (crit[s.criterion] = crit[s.criterion] || {})[s.option] = s.score; });
  if (Object.keys(crit).length) body += `<div class="h4">Scores (1 to 5)</div><div class="sbars">${Object.entries(crit).map(([c, o]) => Object.entries(o).map(([opt, s]) =>
     `<div class="sbar"><span>${esc(c)}, ${esc(opt)}</span><span class="trk ${opt.toLowerCase()}"><i style="--w:${s / 5 * 100}%"></i></span><span class="mono">${s}</span></div>`).join("")).join("")}</div>`;
  $("#sideBody").innerHTML = body;
  const m = $("#moreClaims"); if (m) m.addEventListener("click", () => { state.showAllClaims = showAll ? null : id; panelAdviser(id); });
}
function rank(v) { const s = upper(v && v.status); return s === "STRIKE" ? 2 : s === "FLAG" ? 1 : 0; }

function panelVerdict() {
  const vm = state.vm, m = vm.memo || {};
  $("#sideTitle").textContent = "Verdict";
  const v = upper(vm.final && vm.final.verdict) || "PENDING";
  const revs = Math.max(0, vm.finals.length - 1);
  const k = stanceKey(m.stance);
  const nums = (m.key_numbers || []).slice(0, 4);
  const conds = (m.conditions || []).map(c => withChips(c));
  $("#sideBody").innerHTML = `
    <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap"><span class="vd-word">${esc(vlab(v))}</span><span class="pill sp-${k}">${esc(k === "c" ? "Conditional" : stanceLabel(k))}</span></div>
    <p class="muted" style="font-size:13px">${v === "WRONG" ? `The quality check caught an error the scribe couldn't fix in ${revs} rewrite${revs === 1 ? "" : "s"}, so the memo was held back. Open Quality check to see what it caught before you decide.` : v === "INCOMPLETE" ? `What the memo says checks out, but evidence or a condition is missing${revs ? ` after ${revs} rewrite${revs === 1 ? "" : "s"}` : ""}. Open Quality check to see what.` : revs === 0 ? "Passed the moderator's final check first time." : `The moderator sent the memo back ${revs === 1 ? "once" : revs + " times"} before this verdict.`}</p>
    <p class="rec">${esc(m.recommendation || "")}</p>
    ${nums.length ? `<div class="stats">${nums.map(n => `<div class="stat"><b class="tabnum">${esc(fmtNum(n.value, n.unit).replace(/\s*\/\s*month$/, "").replace(/ of volume$/, ""))}</b><span>${esc(n.text)}</span></div>`).join("")}</div>` : ""}
    ${conds.length ? `<div class="h4">Conditions before any volume moves</div><ul class="conds">${conds.map(w => `<li><span>${esc(w.text)}</span></li>`).join("")}</ul>` : ""}
    <div class="row-actions"><button class="btn" type="button" data-sheet="memo">Read the full recommendation</button><button class="btn primary" type="button" data-sheet="sign">Approve or override</button></div>`;
}
function panelStart() {
  $("#sideTitle").textContent = "Start here";
  const runs = state.mode === "live" ? (state.runs || []).slice(0, 8) : [];
  const row = (id, label, v, sub) => `<li><button class="sg" type="button" data-open-run="${esc(id)}"><div><b>${esc(label)}</b><span>${esc(sub)}</span></div>${v ? `<span class="vt ${esc(v)}">${esc(vlab(v))}</span>` : "<span></span>"}<span class="arr">→</span></button></li>`;
  const list = runs.map(r => {
    const fv = r.final_verdict; const v = upper(typeof fv === "string" ? fv : fv && fv.verdict) || upper(r.status);
    const dec = r.decision_id || (r.brief && r.brief.decision_id) || "";
    const m = state.meta[r.run_id] || {}; const q = r.question || (r.brief && r.brief.question) || m.question || dec || "Untitled decision";
    return row(r.run_id, q, runVerdict(r), [dec || m.decision_id, fmtTime(r.created_at)].filter(Boolean).join(" · "));
  }).join("") + row("sample", "DEC-001 sample decision", "PASS", "Built-in example. Works offline.");
  $("#sideBody").innerHTML = `<p class="lede">The arena is empty. Put a new decision in front of the advisers, or reopen a debate that already ran.</p>
    <button class="sg big" type="button" data-sheet="new"><span class="gi">+</span><div><b>Start a new decision</b><span>Write the question and options. The right advisers join automatically.</span></div><span class="arr">→</span></button>
    <div class="h4">Or open a saved run</div>
    <ul class="runs">${list}</ul>
    ${state.mode === "live" && !runs.length ? `<p class="muted">No saved runs yet. Your first decision will appear here.</p>` : ""}`;
  if (state.mode === "live") enrichRuns();
}
function panelFeed() {
  $("#sideTitle").textContent = "Session log";
  const items = state.feed.slice().reverse();
  $("#sideBody").innerHTML = items.length ? `<ul class="feed">${items.map((e, i) => `<li${i ? ' style="animation:none"' : ""}><time>${esc(e.t)}</time><span>${e.html}</span></li>`).join("")}</ul>` : `<p class="lede">The log fills in as the session runs: who joined, who made their case, what the moderator struck.</p>`;
}
function feed(html, kind) { state.feed.push({ t: $("#clock").textContent, html, kind }); if (state.panel && state.panel.kind === "feed") panelFeed(); }

async function getFact(id) {
  if (state.mode === "live") { try { const r = await fetch(`/facts/${encodeURIComponent(id)}`); if (r.ok) { const f = await r.json(); FACTS[id] = { ...(FACTS[id] || {}), ...f }; } } catch (e) {} }
  return FACTS[id];
}
async function panelEvidence(id) {
  $("#sideTitle").textContent = "Evidence";
  if (id === "BRIEF") { $("#sideBody").innerHTML = `<div class="fid">BRIEF</div><p class="lede">${esc(state.vm ? state.vm.brief.question : "")}</p><p class="muted">Numbers quoted from the decision brief itself.</p>`; return; }
  const f = await getFact(id);
  if (!f) { $("#sideBody").innerHTML = `<div class="fid">${esc(id)}</div><p class="muted">This evidence ID isn't in the fact store.</p>`; return; }
  const citing = []; (state.vm ? state.vm.positions : []).forEach(p => (p.claims || []).forEach(c => { if ((c.evidence_ids || []).includes(id)) citing.push({ who: advName(p.adviser_id, true), c, v: state.vm.verdicts[c.claim_id] }); }));
  const val = f.value_num != null && f.value_num !== "" ? fmtNum(f.value_num, "") : (f.value_text || "");
  $("#sideBody").innerHTML = `<div class="fid">${esc(f.fact_id || id)}</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap"><span class="rel ${esc(f.reliability)}">${esc(f.reliability || "unknown")} reliability</span><span class="rel">${esc(advName(f.domain))}</span></div>
    ${f.superseded_by ? `<div class="old">Out of date. Replaced by ${chip(f.superseded_by)}</div>` : ""}
    <p class="lede" style="color:var(--ink);font-size:16px">${esc(f.statement)}</p>
    ${val ? `<div class="fval tabnum">${esc(val)}<small>${esc(f.unit || "")}</small></div>` : ""}
    <dl class="dl"><dt>Source</dt><dd>${esc(f.source_ref)}</dd><dt>As of</dt><dd>${esc(f.as_of)}</dd><dt>About</dt><dd>${esc(f.entity)}</dd></dl>
    ${citing.length ? `<div class="cited"><span class="h4">Cited in this debate</span>${citing.map(x => { const s = upper(x.v && x.v.status); return `<div><b style="font-weight:500">${esc(x.who)}</b> ${s === "STRIKE" ? '<span class="vt STRUCK">STRUCK</span>' : s === "FLAG" ? '<span class="vt FLAGGED">FLAGGED</span>' : ""}<br>${esc(x.c.text)}</div>`; }).join("")}</div>` : ""}`;
}

/* ---------------- sheets ---------------- */
function openSheet(kind, refresh) {
  state.sheet = kind;
  const vm = state.vm; const T = $("#sheetTitle"), S = $("#sheetSub"), B = $("#sheetBody");
  $$("#path .step, .brief-link").forEach(b => b.classList.toggle("on", b.dataset.sheet === kind));
  try {
    if (kind === "brief") { T.textContent = "The question"; S.textContent = "What the advisers were asked to decide, and how options are judged"; B.innerHTML = sheetBrief(vm); }
    if (kind === "routing") { T.textContent = "Who joined, and why"; S.textContent = "Each adviser's expertise is compared with the question by meaning"; B.innerHTML = sheetRouting(vm); }
    if (kind === "debate") { T.textContent = "Their arguments"; S.textContent = debateSummary(vm) + " Click an adviser for the detail."; B.innerHTML = sheetDebate(vm); }
    if (kind === "checks") { T.textContent = "Fact checks"; S.textContent = "The moderator checked every claim against the evidence before anything reached the memo"; B.innerHTML = sheetChecks(vm); }
    if (kind === "scorecard") { T.textContent = "Scores"; S.textContent = "Weighted scores computed in code from each owner's ratings. Nobody can edit them."; B.innerHTML = sheetScorecard(vm); }
    if (kind === "memo") { T.textContent = "Recommendation"; S.textContent = "Written from checked claims only"; B.innerHTML = sheetMemo(vm); }
    if (kind === "final") { T.textContent = "Quality check"; S.textContent = "Does the memo make sense, is it complete, is it correct?"; B.innerHTML = sheetFinal(vm); }
    if (kind === "sign") { T.textContent = "Your decision"; S.textContent = "The recommendation is advisory. You approve or override it, and the record keeps who and why."; B.innerHTML = sheetSign(vm); wireSign(); }
    if (kind === "new") { T.textContent = "New decision"; S.textContent = state.mode === "live" ? "Starts a real debate, about 5 to 8 minutes" : "Preview: replays the sample debate"; B.innerHTML = sheetNew(); wireNew(); }
  } catch (e) { console.error(e); B.innerHTML = `<p class="muted">Couldn't show this view.</p>`; }
  if (refresh) return;
  $("#sheet").classList.add("on"); $("#scrim").classList.add("on"); $("#sheet").setAttribute("aria-hidden", "false");
  setTimeout(() => $("#sheetClose").focus(), 60);
}
function closeSheet() { state.sheet = null; $("#sheet").classList.remove("on"); $("#scrim").classList.remove("on"); $("#sheet").setAttribute("aria-hidden", "true"); $$("#path .step").forEach(b => b.classList.remove("on")); }

function sheetBrief(vm) {
  const b = vm.brief;
  return `<div class="grid2"><div class="box"><h3>Question</h3><p style="font-size:17px">${esc(b.question)}</p>
    <div class="opts">${Object.entries(vm.opts).map(([k, v]) => `<span class="${k.toLowerCase()}"><i>${esc(k)}</i>${esc(v)}</span>`).join("")}</div>
    ${b.context ? `<p class="muted" style="font-size:14px">${esc(b.context)}</p>` : ""}</div>
    <div class="box"><h3>Constraints</h3><ul class="cons">${(b.constraints || []).map(c => `<li>${esc(c)}</li>`).join("")}</ul>
    <h3>Criteria and weights</h3><div class="sbars">${(b.criteria || []).map(c => `<div class="sbar"><span>${esc(c.name)}</span><span class="trk s"><i style="--w:${Math.round(c.weight * 100)}%"></i></span><span class="mono">${Math.round(c.weight * 100)}</span></div>`).join("")}</div></div></div>`;
}
function debateSummary(vm) {
  let k = 0, f = 0, s = 0; vm.positions.forEach(p => { const t = claimStats(vm, p.adviser_id); k += t.k; f += t.f; s += t.s; });
  return vm.positions.length ? `${vm.positions.length} advisers, ${k + f + s} claims. The moderator struck ${s} and flagged ${f}.` : "";
}
function sheetRouting(vm) {
  const rows = [...vm.scores].sort((a, b) => b.sim - a.sim);
  const top = rows.length ? rows[0].sim : 0, margin = Math.max(0, top - ROUTING.margin);
  return `<div class="grid2"><div class="box"><h3>Match with the question</h3>
      ${rows.map(r => `<div class="rrow"><div class="t"><span>${esc(advName(r.id))}</span><span class="mono">${r.sim.toFixed(2)}</span></div><span class="trk s"><i style="--w:${r.sim * 100}%"></i></span><span class="w ${/rule/i.test(r.reason) ? "rule" : ""}">${r.selected ? "Joined" : "Not called"}${r.reason ? ": " + esc(r.reason) : ""}${r.cap ? `. Closest expertise: ${esc(r.cap)}` : ""}</span></div>`).join("")}</div>
    <div class="box"><h3>How advisers are chosen</h3>
      <p class="explain">Colosseum turns the question into a short routing query, then compares it with what each adviser is good at. Advisers within ${ROUTING.margin} of the best match join, as long as they score at least ${ROUTING.floor}. Some rules always add an adviser, for example a customer-facing change always brings in Customer.</p>
      <p class="explain">For this question the best match was ${top.toFixed(2)}, so the cut-off was ${margin.toFixed(2)}.</p>
      ${vm.routing.routing_query ? `<h3>Routing query</h3><p class="explain">${esc(vm.routing.routing_query)}</p>` : ""}</div></div>`;
}
function sheetDebate(vm) {
  const order = [...vm.scores].sort((a, b) => b.sim - a.sim).map(r => r.id).filter(id => vm.posById[id]); vm.positions.forEach(p => { if (!order.includes(p.adviser_id)) order.push(p.adviser_id); });
  const cs = state.cseen[vm.id || "_"] = state.cseen[vm.id || "_"] || new Set();
  const cards = order.map(id => { const p = vm.posById[id], k = stanceKey(p.stance), t = claimStats(vm, id); const isNew = cs.primed && !cs.has(id); cs.add(id);
    return `<button type="button" class="acard" data-open-adv="${esc(id)}" style="${isNew ? "animation:bubin .6s var(--ease) both" : ""}"><div class="top-r"><h3>${esc(advName(id, true))}</h3><span class="pill sp-${k}">${esc(stanceLabel(k))}</span></div><p>${esc(p.summary)}</p>
      <div class="tally"><span class="k">${t.k} kept</span><span class="f">${t.f} flagged</span><span class="s">${t.s} struck</span>${(p.blocking_findings || []).length ? '<span class="s">blocking issue</span>' : ""}</div></button>`; }).join("");
  setTimeout(() => cs.primed = true, 0);
  return `<div class="cards">${cards || '<p class="muted">No positions yet.</p>'}</div>`;
}
function sheetChecks(vm) {
  const nClaims = vm.positions.reduce((a, p) => a + (p.claims || []).length, 0);
  if (!vm.positions.length) return `<p class="lede">No adviser positions in this run yet, so there is nothing to check.</p>`;
  if (!Object.keys(vm.verdicts).length) return `<p class="lede">The advisers made ${nClaims} claim${nClaims === 1 ? "" : "s"}, but this run record has no fact-check results${["done", "failed"].includes(vm.status) ? "" : " yet. They appear once the moderator finishes"}.</p>`;
  let k = 0, f = 0, x = 0; vm.positions.forEach(p => { const t = claimStats(vm, p.adviser_id); k += t.k; f += t.f; x += t.s; });
  const items = [];
  vm.positions.forEach(p => (p.claims || []).forEach(c => { const v = vm.verdicts[c.claim_id]; const st = upper(v && v.status); if (st === "STRIKE" || st === "FLAG") items.push({ p, c, v, st }); }));
  items.sort((a, b) => (a.st === "STRIKE" ? 0 : 1) - (b.st === "STRIKE" ? 0 : 1));
  const blocks = []; vm.positions.forEach(p => (p.blocking_findings || []).forEach((b, i) => blocks.push({ p, b, v: vm.verdicts[`${p.adviser_id}-B${i + 1}`] })));
  return `<div class="bigs"><div class="stat"><b style="color:var(--keep)">${k}</b><span>claims held up against the evidence</span></div>
      <div class="stat"><b style="color:var(--flag)">${f}</b><span>flagged: kept, with a warning</span></div>
      <div class="stat"><b style="color:var(--strike)">${x}</b><span>struck: removed before the memo</span></div></div>
    <div class="grid2" style="margin-top:18px"><div class="box"><h3>What the moderator caught</h3>
      ${items.length ? `<ul class="mod-list">${items.map(({ p, c, v, st }) => `<li><span class="vt ${st === "STRIKE" ? "STRUCK" : "FLAGGED"}">${st === "STRIKE" ? "STRUCK" : "FLAGGED"}</span><div><b style="font-weight:500">${esc(advName(p.adviser_id, true))}:</b> ${esc(c.text)}<div class="muted" style="font-size:13px;margin-top:4px">${esc(v.reason)}</div><div class="chips">${(c.evidence_ids || []).map(chip).join("")}</div></div></li>`).join("")}</ul>` : `<p class="explain">Every claim matched its evidence.</p>`}</div>
    <div class="box"><h3>How claims are checked</h3><p class="explain">Code checks come first: does the evidence exist, is it current, do the numbers match. Then Gemini checks whether the evidence really supports the claim. A blocking issue can't be struck; it stays until a condition clears it or a person overrides it.</p>
      ${blocks.map(({ p, b, v }) => `<div class="blk"><h4>Blocking issue from ${esc(advName(p.adviser_id, true))}</h4><div>${esc(b.text)}</div><div class="fix"><b style="color:var(--ink);font-weight:500">Fix:</b> ${esc(b.fix)}</div>${v ? `<div class="muted" style="font-size:12.5px">${esc(v.reason)}</div>` : ""}</div>`).join("")}</div></div>`;
}
function sheetFinal(vm) {
  return `<div class="grid2"><div class="box"><h3>Drafts</h3><ul class="mod-list">${vm.finals.map((f, i) => { const v = upper(f.verdict); const iss = (f.issues || []);
      return `<li><span class="vt ${esc(v)}">${esc(vlab(v))}</span><div><b style="font-weight:500">Draft ${i + 1}</b>${v === "PASS" ? `<div class="muted" style="font-size:13px;margin-top:4px">Makes sense, complete and correct.</div>` : iss.map(x => `<div class="muted" style="font-size:13px;margin-top:4px">${esc(x.detail)}</div>`).join("")}${f.revision_instructions ? `<div style="font-size:13px;margin-top:6px;color:var(--ink-2)">Sent back with: ${esc(f.revision_instructions)}</div>` : ""}</div></li>`; }).join("") || "<li><div>No check yet.</div></li>"}</ul></div>
    <div class="box"><h3>What the quality check does</h3><p class="explain">Before you see the memo, the moderator checks that every adviser is represented, every criterion is covered, numbers match the evidence, blocking issues are handled, and dissent is recorded. If something is missing it sends the memo back to be rewritten, up to two times.</p></div></div>`;
}
function sheetScorecard(vm) {
  const sc = vm.scorecard; if (!sc || !(sc.rows || []).length) return `<p class="muted">No scorecard yet.</p>`;
  const opts = Object.keys(vm.opts).length ? Object.keys(vm.opts) : Object.keys(sc.weighted_totals || {});
  const owners = {}; (vm.brief.criteria || []).forEach(c => owners[c.name] = c.owners || []);
  const tot = sc.weighted_totals || {}; const best = opts.reduce((b, o) => (tot[o] ?? -1) > (tot[b] ?? -1) ? o : b, opts[0]);
  const blocked = sc.blocked_options || {};
  return `<div class="tw"><table><thead><tr><th>Criterion</th><th>Weight</th>${opts.map(o => `<th>Option ${esc(o)}</th>`).join("")}<th>Evidence</th></tr></thead><tbody>
    ${sc.rows.map(r => `<tr><td>${esc(r.criterion)}<div class="f">${(r.owners || owners[r.criterion] || []).map(o => esc(advName(o, true))).join(", ")}</div></td><td class="mono">${Math.round((r.weight || 0) * 100)}%</td>
      ${opts.map(o => { const s = r.scores ? r.scores[o] : null; return `<td><div class="sbar" style="grid-template-columns:minmax(80px,1fr) 30px"><span class="trk ${o.toLowerCase()}"><i style="--w:${s == null ? 0 : s / 5 * 100}%"></i></span><span class="mono">${s == null ? "–" : esc(s)}</span></div></td>`; }).join("")}
      <td>${(r.evidence_ids || []).slice(0, 3).map(chip).join(" ")}</td></tr>`).join("")}
    <tr><td><b style="font-weight:500">Weighted total</b><div class="f">out of 5</div></td><td></td>${opts.map(o => `<td><span class="tot tabnum ${o === best ? "win" : ""}">${tot[o] != null ? Number(tot[o]).toFixed(2) : "–"}</span>${(blocked[o] || []).length ? ' <span class="vt WRONG">BLOCKED</span>' : ""}</td>`).join("")}<td></td></tr>
    </tbody></table></div>${Object.entries(blocked).filter(([, v]) => (v || []).length).map(([o, v]) => `<div class="blocked"><b>Option ${esc(o)} is blocked.</b> ${v.map(esc).join(" ")} A condition must clear it, or a person must override it.</div>`).join("")}`;
}
function sheetMemo(vm) {
  const m = vm.memo; if (!m) return `<p class="muted">No memo yet.</p>`;
  const L = arr => (arr || []).map(withChips);
  return `<div class="grid2">
    <div class="box"><h3>Recommendation</h3><p class="rec">${esc(m.recommendation)}</p>
      <h3>Conditions</h3><ul class="conds">${L(m.conditions).map(w => `<li><div>${esc(w.text)}<div class="chips">${w.ids.map(chip).join("")}${w.by ? `<span class="muted" style="font-size:12px">Raised by ${esc(w.by)}</span>` : ""}</div></div></li>`).join("")}</ul></div>
    <div class="box"><h3>Phased plan</h3><ol class="plan">${L(m.phased_plan).map(w => `<li><div>${esc(w.text)} ${w.ids.map(chip).join(" ")}</div></li>`).join("") || "<li><div>Single phase.</div></li>"}</ol>
      <h3>Dissent on record</h3><ul class="dis">${L(m.dissent).map(w => `<li>${esc(w.text)} ${w.ids.map(chip).join(" ")}</li>`).join("") || "<li>No adviser disagreed.</li>"}</ul></div>
    <div class="box"><h3>Key numbers</h3><div class="tw"><table><tbody>${(m.key_numbers || []).map(k => `<tr><td>${esc(k.text)}<div class="f">${esc(k.formula || "")}</div></td><td class="num">${esc(fmtNum(k.value, k.unit))}</td><td>${(k.evidence_ids || []).map(chip).join(" ")}</td></tr>`).join("")}</tbody></table></div></div>
    <div class="box"><h3>Numbers to watch</h3><ul class="dis">${L(m.numbers_to_watch).map(w => `<li>${esc(w.text)} ${w.ids.map(chip).join(" ")}</li>`).join("")}</ul>
      <h3>Moderator's final check</h3><div class="drafts">${vm.finals.map((f, i) => { const v = upper(f.verdict); const iss = (f.issues || [])[0]; return `<div>Draft ${i + 1} <span class="vt ${esc(v)}">${esc(vlab(v))}</span> ${esc(v === "PASS" ? "Makes sense, complete and correct." : iss ? iss.detail : "")}</div>`; }).join("")}</div></div>
  </div>`;
}
function sheetSign(vm) {
  const items = vm.decisions.map(d => `<li><div><span class="act">${String(d.action || "").toLowerCase() === "override" ? "Overrode" : "Approved"}</span> by <b style="font-weight:500">${esc(d.decided_by || "Unnamed")}</b><div class="muted mono" style="font-size:12px">${esc(fmtTime(d.decided_at))}</div>${(d.reason || d.note) ? `<div class="muted">${esc(d.reason || d.note)}</div>` : ""}</div></li>`).join("");
  return `<div class="grid2"><form class="box" id="signForm" novalidate><h3>Your decision</h3>
      ${state.mode === "live" ? codeField("signCode") : ""}
      <div class="field"><label for="signName">Name and role</label><input id="signName" autocomplete="name" placeholder="For example, Priya Raman, Head of Payments"></div>
      <div class="field"><label for="signReason">Reason (needed to override)</label><textarea id="signReason" placeholder="What you decided and why"></textarea><span class="err" id="signErr" role="alert"></span></div>
      <div class="row-actions"><button class="btn primary" type="submit">Approve recommendation</button><button class="btn danger" type="button" id="overrideBtn">Override</button></div></form>
    <div class="box"><h3>Decision record</h3><ul class="log">${items || '<li style="grid-template-columns:1fr" class="muted">No one has signed this decision yet. Every approval and override is kept.</li>'}</ul></div></div>`;
}
function wireSign() {
  $("#signForm").addEventListener("submit", e => { e.preventDefault(); signOff("approve"); });
  $("#overrideBtn").addEventListener("click", () => signOff("override"));
}
async function signOff(action) {
  const vm = state.vm; if (!vm) return;
  const name = $("#signName").value.trim(), reason = $("#signReason").value.trim(), err = $("#signErr");
  if (!name) { err.textContent = "Add your name and role so the record shows who decided."; $("#signName").focus(); return; }
  if (action === "override" && !reason) { err.textContent = "An override needs a reason. Say what you decided instead and why."; $("#signReason").focus(); return; }
  err.textContent = "";
  if (state.mode === "live") {
    try { const r = await fetch(`/runs/${encodeURIComponent(vm.id)}/decision`, { method: "POST", headers: writeHeaders("signCode"), body: JSON.stringify({ action, reason, decided_by: name }) });
          if (r.status === 401 || r.status === 403) { err.textContent = "This demo needs a valid access code to record a decision."; if (!state.needCode) { state.needCode = true; openSheet("sign", true); wireSign(); $("#signErr").textContent = "This demo needs a valid access code to record a decision."; } return; }
          if (!r.ok) throw new Error(`the API answered ${r.status}`); }
    catch (e) { err.textContent = "Couldn't save the decision: " + e.message + ". Try again."; return; }
  }
  vm.decisions.push({ action, reason, decided_by: name, decided_at: new Date().toISOString() });
  openSheet("sign", true);
  toast((action === "override" ? "Override recorded" : "Approval recorded") + (state.mode === "live" ? "." : " in this preview only."));
}
function sheetNew() {
  const brief = state.newBrief || (state.vm && state.vm.brief && state.vm.brief.question ? state.vm.brief : SAMPLE.brief);
  state.newBrief = brief;
  return `<div class="grid2"><div class="box">
      <div class="field"><label for="nq">Question</label><textarea id="nq">${esc(brief.question)}</textarea></div>
      <div class="field"><label for="nA">Option A</label><input id="nA" value="${esc((brief.options || {}).A)}"></div>
      <div class="field"><label for="nB">Option B</label><input id="nB" value="${esc((brief.options || {}).B)}"></div>
      <div class="field"><label for="nx">Context</label><textarea id="nx">${esc(brief.context || "")}</textarea></div></div>
    <div class="box"><div class="field"><label for="nc">Constraints, one per line</label><textarea id="nc" style="min-height:120px">${esc((brief.constraints || []).join("\n"))}</textarea></div>
      <div class="field"><span>Criteria and weights (%)</span>${(brief.criteria || []).map((c, i) => `<div class="crit-row"><input id="cn${i}" value="${esc(c.name)}" aria-label="Criterion"><input class="cw tabnum" id="cw${i}" type="number" min="0" max="100" value="${Math.round(c.weight * 100)}" aria-label="Weight"></div>`).join("")}<span class="wsum" id="wsum"></span></div>
      ${state.mode === "live" ? codeField("ncode") : ""}
      <button class="btn primary" id="startBtn" type="button">Start the debate</button></div></div>`;
}
async function wireNew() {
  if (state.mode === "live" && !state.briefFetched) { state.briefFetched = true; try { const r = await fetch("/briefs/sample"); if (r.ok) { state.newBrief = await r.json(); openSheet("new", true); return; } } catch (e) {} }
  const brief = state.newBrief;
  const sum = () => { const t = $$(".cw").reduce((a, i) => a + (Number(i.value) || 0), 0); const el = $("#wsum"); el.textContent = `Weights add up to ${t}%` + (t === 100 ? "" : ". They must add up to 100%."); el.className = "wsum " + (t === 100 ? "ok" : "bad"); $("#startBtn").disabled = t !== 100; };
  $$(".cw").forEach(i => i.addEventListener("input", sum)); sum();
  $("#startBtn").addEventListener("click", async () => {
    const nb = JSON.parse(JSON.stringify(brief));
    nb.question = $("#nq").value.trim(); nb.options = { A: $("#nA").value.trim(), B: $("#nB").value.trim() };
    nb.constraints = $("#nc").value.split("\n").map(s => s.trim()).filter(Boolean); nb.context = $("#nx").value.trim();
    nb.criteria = (nb.criteria || []).map((c, i) => ({ ...c, name: $("#cn" + i).value.trim() || c.name, weight: (Number($("#cw" + i).value) || 0) / 100 }));
    if (state.mode !== "live") { closeSheet(); if (state.runId !== "sample") await openRun("sample", true); replay(); return; }
    $("#startBtn").disabled = true; $("#startBtn").textContent = "Starting…";
    try {
      const r = await fetch("/decisions", { method: "POST", headers: writeHeaders("ncode"), body: JSON.stringify(nb) });
      if (r.status === 401 || r.status === 403) { if (!state.needCode) { state.needCode = true; openSheet("new", true); wireNew(); } $("#startBtn").disabled = false; $("#startBtn").textContent = "Start the debate"; toast("This demo needs a valid access code to start a debate."); $("#ncode") && $("#ncode").focus(); return; }
      if (r.status === 429) throw new Error("a debate is already running. Try again in a few minutes");
      if (!r.ok) throw new Error(`the API answered ${r.status}. ${await r.text()}`);
      const { run_id } = await r.json();
      closeSheet(); toast("Debate started. The advisers are taking their seats.");
      state.feed = []; feed('<span class="e-sig">Session opened</span>', "sig");
      await loadRunList(); await openRun(run_id); openPanel("feed");
    } catch (e) { $("#startBtn").disabled = false; $("#startBtn").textContent = "Start the debate"; toast("Couldn't start the debate: " + e.message); }
  });
}

/* ---------------- replay: the orchestrated moment ---------------- */
async function replay() {
  const vm = state.vm; if (!vm || state.replaying || vm.status === "idle") return;
  if (reduced) { toast("Replay is off because your system asks for reduced motion."); return; }
  state.replaying = true; state.feed = []; closeSheet(); openPanel("feed");
  const t0 = performance.now(), total = vm.metrics.latency_s || 400, DUR = 13000;
  const tick = setInterval(() => { $("#clock").textContent = fmtClock(Math.min(total, (performance.now() - t0) / DUR * total)); }, 80);
  const step = async (i, working, ms) => { applyStage(i, working); setSession("live", Math.max(0, i)); if (ms) await sleep(ms); };
  $$("#arena .seat").forEach(g => g.classList.remove("arrived", "pop"));
  await step(-1, false, 500);
  feed('<span class="e-sig">Session opened</span>');
  await step(0, false, 600);
  vm.selected.forEach((id, n) => setTimeout(() => feed(`<span class="e-sig">${esc(advName(id, true))}</span> takes a seat`), n * 90));
  await sleep(900);
  state.replayArrived = 0; await step(1, true, 700);
  const order = ["business", "infosec", "customer", "legal", "ops", "finance"].filter(id => vm.posById[id] && vm.selected.includes(id));
  vm.positions.forEach(p => { if (!order.includes(p.adviser_id) && vm.selected.includes(p.adviser_id)) order.push(p.adviser_id); });
  for (const id of order) {
    const g = $(`#arena .seat[data-adv="${CSS.escape(id)}"]`); if (g) { g.classList.add("arrived", "pop"); }
    $(`#ln-${CSS.escape(id)}`)?.classList.add("arrived");
    state.replayArrived++; setSession("live", 1); renderTimeline(); pulseTo(id);
    bubble(id, headline(vm.posById[id]), advName(id, true) + " made the case", "", 1700, "arrive");
    feed(`<span class="e-ok">${esc(advName(id, true))}</span> made the case: ${esc(stanceShort(stanceKey(vm.posById[id].stance)))}`);
    await sleep(520);
  }
  await sleep(300);
  await step(2, false, 0);
  const app = $("#app"); app.classList.add("scanning");
  await sleep(700);
  applyStage(3, false); setSession("live", 2);
  Object.values(vm.verdicts).filter(v => ["STRIKE", "FLAG"].includes(upper(v.status))).forEach((v, n) => {
    const owner = (v.claim_id || "").split("-")[0]; const isB = /-B\d+$/.test(v.claim_id);
    if (isB) return;
    const claim = (vm.posById[owner]?.claims || []).find(c => c.claim_id === v.claim_id);
    const kind = upper(v.status) === "STRIKE" ? "strike" : "flag";
    setTimeout(() => { bubble(owner, v.reason, (kind === "strike" ? "Struck by the moderator" : "Flagged by the moderator"), kind, 2400, "mod");
      feed(`<span class="e-${kind}">Moderator ${kind === "strike" ? "struck" : "flagged"}</span> a ${esc(advName(owner, true))} claim`); }, n * 450);
  });
  vm.blocking.forEach(id => setTimeout(() => feed(`<span class="e-strike">${esc(advName(id, true))} blocking finding</span> kept, with a fix`), 900));
  await sleep(2600); app.classList.remove("scanning");
  await step(3, false, 900); setSession("live", 3); feed('<span class="e-sig">Scorecard</span> computed');
  await step(4, false, 1000); feed('<span class="e-sig">The Scribe</span> drafted the memo');
  await step(5, false, 600);
  if (vm.finals.length > 1) { feed(`<span class="e-flag">Moderator sent the memo back</span>: ${esc(((vm.finals[0].issues || [])[0] || {}).detail || "")}`); await sleep(900); }
  applyStage(6, false);
  $$("#arena .seat").forEach(g => g.classList.remove("arrived", "pop"));
  clearInterval(tick); state.replaying = false; state.replayArrived = null;
  updateClock(); setSession(arenaKind(vm), 6); burst(); renderTimeline();
  feed(`<span class="e-ok">Verdict: ${esc(vsent(upper(vm.final && vm.final.verdict)))}</span>. Ready for sign-off.`);
  $("#replayBtn").disabled = false;
  sweepPath();
  await sleep(1200); openPanel("verdict");
}

/* short entrance whenever a finished run is opened */
async function entrance() {
  const vm = state.vm; if (!vm || vm.status !== "done") return;
  if (reduced) { applyStage(6); setSession("done", 6); return; }
  const token = state.tok = (state.tok || 0) + 1;
  applyStage(-1); setSession("live", 0);
  await sleep(60); if (token !== state.tok) return;
  applyStage(0); await sleep(450); if (token !== state.tok) return;
  applyStage(1, true); setSession("live", 1); await sleep(650); if (token !== state.tok) return;
  applyStage(3); setSession("live", 2); await sleep(550); if (token !== state.tok) return;
  applyStage(6); setSession("done", 6); burst();
  setTimeout(() => { sweepPath(); setTimeout(showCoach, 1400); }, 700);
}

/* ---------------- data ---------------- */
function getCode() { if (state.code == null) { try { state.code = sessionStorage.getItem("colosseum-code") || ""; } catch (e) { state.code = ""; } } return state.code; }
function setCode(v) { state.code = v; try { sessionStorage.setItem("colosseum-code", v); } catch (e) {} }
function codeField(id) { return state.needCode ? `<div class="field"><label for="${id}">Access code</label><input id="${id}" type="password" autocomplete="off" value="${esc(getCode())}" placeholder="Shared with the people invited to try this demo"></div>` : ""; }
function writeHeaders(id) { const el = id && $("#" + id); if (el) setCode(el.value.trim()); const h = { "Content-Type": "application/json" }; if (getCode()) h["X-Access-Code"] = getCode(); return h; }
async function fetchJSON(url, ms = 4000) {
  const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), ms);
  try { const r = await fetch(url, { signal: ctl.signal, headers: { Accept: "application/json" } }); if (!r.ok) throw new Error(String(r.status));
        if (!(r.headers.get("content-type") || "").includes("json")) throw new Error("not json"); return await r.json(); }
  finally { clearTimeout(t); }
}
async function detectApi() {
  if (/(^|[?#&])demo\b/.test(location.search + location.hash)) return false;
  try { const r = await fetchJSON("/runs?limit=25", 15000);
        try { const h = await fetchJSON("/health", 5000); state.needCode = !!(h && h.access_code_required); } catch (e) {}
        return Array.isArray(r) ? r : Array.isArray(r.runs) ? r.runs : false; } catch (e) { return false; }
}
async function loadRunList() { try { const r = await fetchJSON("/runs?limit=25"); state.runs = Array.isArray(r) ? r : (r.runs || []); } catch (e) {} fillPicker(); }
/* ---------------- past decisions: search box (centre) + decision records (tile) ---------------- */
function runVerdict(r) {
  const st = String(r.status || "").toLowerCase();
  const fv = r.final_verdict; const v = upper(typeof fv === "string" ? fv : fv && fv.verdict) || upper((state.meta[r.run_id] || {}).verdict);
  if (st === "failed") return "FAILED";
  if (st && st !== "done") return "RUNNING";
  return v || "DONE";
}
const VWORD = { PASS: "Passed the quality check", INCOMPLETE: "Needs evidence: something is missing", WRONG: "Held back: the check caught an error", FAILED: "Run stopped", RUNNING: "In session", DONE: "Finished" };
const recLabel = st => { const k = stanceKey(st); return k === "c" ? "Conditional move" : k === "b" ? "Recommends B" : k === "a" ? "Recommends A" : ""; };
function sampleItem() {
  const m = SAMPLE.memo || last(SAMPLE.memo_versions || []) || {};
  return { id: "sample", q: (SAMPLE.brief && SAMPLE.brief.question) || "DEC-001 sample decision", dec: "DEC-001", v: "PASS", when: "Built-in example",
    rec: recLabel(m.stance), text: m.recommendation || "", conds: m.conditions || [], sample: true, loaded: true };
}
function histItems() {
  return (state.mode === "live" ? state.runs : []).map(r => {
    const m = state.meta[r.run_id] || {};
    return { id: r.run_id, q: r.question || (r.brief && r.brief.question) || m.question || "", dec: r.decision_id || (r.brief && r.brief.decision_id) || m.decision_id || "",
      v: runVerdict(r), when: fmtTime(r.created_at), rec: recLabel(r.stance || m.stance), text: m.rec || "", conds: m.conds || [], loaded: !!m.loaded };
  });
}
function hlText(text, terms) {
  text = String(text || ""); if (!terms.length) return esc(text);
  const low = text.toLowerCase(), on = new Array(text.length).fill(false);
  terms.forEach(t => { let i = low.indexOf(t); while (t && i >= 0) { for (let j = i; j < i + t.length; j++) on[j] = true; i = low.indexOf(t, i + t.length); } });
  let out = "", open = false;
  for (let i = 0; i < text.length; i++) { if (on[i] !== open) { out += on[i] ? "<mark>" : "</mark>"; open = on[i]; } out += esc(text[i]); }
  return out + (open ? "</mark>" : "");
}
const ICON_SEARCH = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>';
const ICON_CLOCK = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>';
/* the centre search box, Google style: query – verdict suggestions */
function renderOmni() {
  if (!$("#omni").classList.contains("open")) return;
  const raw = $("#histQ").value.trim(), terms = raw.toLowerCase().split(/\s+/).filter(Boolean);
  const all = histItems(), smp = sampleItem();
  const match = i => { const hay = [i.q, i.dec, i.v, vlab(i.v), VWORD[i.v], i.rec, i.when, i.text].join(" ").toLowerCase(); return terms.every(t => hay.includes(t)); };
  let list = all.filter(match); if (match(smp)) list.push(smp);
  if (!terms.length) list = list.slice(0, 6);
  const row = i => `<button type="button" role="option" class="hrow" data-open-run="${esc(i.id)}">
      <span class="hi">${terms.length ? ICON_SEARCH : ICON_CLOCK}</span>
      <span class="hq">${hlText(i.q || i.dec || "Untitled decision", terms)}<span class="hd"> – <span class="v-${esc(i.v)}">${esc(VWORD[i.v] || i.v)}</span>${i.rec && !["FAILED", "RUNNING"].includes(i.v) ? " · " + esc(i.rec) : ""} · ${esc(i.when)}</span>${state.runId === i.id ? '<span class="now">open now</span>' : ""}</span>
      <span class="vt ${esc(i.v)}">${esc(vlab(i.v))}</span></button>`;
  $("#histList").innerHTML = (terms.length ? "" : `<div class="hl-h">Recent decisions</div>`) +
    (list.map(row).join("") || `<div class="hl-empty">No past decision matches “${esc(raw)}”. Try a word from the question, a verdict like “held back”, or a month.</div>`);
  const n = $$("#histList .hrow").length; state.histAct = Math.max(0, Math.min(state.histAct || 0, n - 1));
  $$("#histList .hrow").forEach((b, k) => b.classList.toggle("act", k === state.histAct));
}
function openOmni() { const o = $("#omni"); if (o.classList.contains("open")) return; closeRec(); o.classList.add("open"); $("#histQ").setAttribute("aria-expanded", "true"); state.histAct = 0; renderOmni(); enrichRuns(); }
function closeOmni() { const o = $("#omni"); if (!o.classList.contains("open")) return; o.classList.remove("open"); $("#histQ").setAttribute("aria-expanded", "false"); }
function moveHist(d) { const rows = $$("#histList .hrow"); if (!rows.length) return; state.histAct = (state.histAct + d + rows.length) % rows.length; rows.forEach((b, k) => b.classList.toggle("act", k === state.histAct)); rows[state.histAct].scrollIntoView({ block: "nearest" }); }
/* the Decision records tile: query | verdict | more details */
function renderRecTile() {
  const items = histItems(), live = state.mode === "live", n = items.length;
  $("#histCount").textContent = live ? (n ? `${n} decision${n === 1 ? "" : "s"}` : "No decisions yet") : "Sample only";
  $("#histDots").innerHTML = (live && n ? items.slice(0, 5) : [{ v: "PASS" }]).map(i => `<i class="${i.v}" title="${esc(VWORD[i.v] || i.v)}"></i>`).join("");
}
function renderRecords() {
  if (!$("#rec").classList.contains("open")) return;
  const items = histItems(); items.push(sampleItem());
  const chev = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="M6 9l6 6 6-6"/></svg>';
  const more = i => {
    if (!i.loaded) return `<p class="muted">Loading the recommendation…</p>`;
    if (i.v === "FAILED") return `<p class="rtxt">This run stopped before a recommendation was written.</p>`;
    if (!i.text) return `<p class="rtxt">No recommendation recorded${i.v === "RUNNING" ? " yet. The debate is still in session" : ""}.</p>`;
    return `<p class="rtxt">${esc(i.text)}</p>${i.conds.length ? `<h5>Conditions before any volume moves</h5><ul>${i.conds.map(c => `<li>${esc(c)}</li>`).join("")}</ul>` : ""}
      <div class="row-actions"><button class="btn primary" type="button" data-open-run="${esc(i.id)}">Open in the arena</button><button class="btn" type="button" data-rec-memo="${esc(i.id)}">Read the full memo</button></div>`;
  };
  $("#recPop").innerHTML = `<div class="rec-top"><b>Decision records</b><span>${items.length - 1 || "No"} past decision${items.length - 1 === 1 ? "" : "s"}, newest first</span><span class="sp">Click a question to open it in the arena</span></div>
    <div class="rec-cols"><span>Decision query</span><span>Verdict</span><span>More details</span></div>
    <div class="rec-body">${items.map(i => `<div class="rec-item ${state.recOpen === i.id ? "open" : ""} ${state.runId === i.id ? "cur" : ""}">
      <div class="rec-row">
        <button class="rec-q" type="button" data-open-run="${esc(i.id)}"><span>${esc(i.q || i.dec || "Untitled decision")}</span><span class="m">${esc([i.dec, i.when].filter(Boolean).join(" · "))}</span></button>
        <div class="rec-v"><span class="vt ${esc(i.v)}">${esc(vlab(i.v))}</span><span>${esc((["FAILED", "RUNNING"].includes(i.v) ? "" : i.rec) || VWORD[i.v] || "")}</span></div>
        <button class="rec-more-btn" type="button" data-rec-more="${esc(i.id)}" aria-expanded="${state.recOpen === i.id}">${state.recOpen === i.id ? "Show less" : "Read more"} ${chev}</button>
      </div>
      <div class="rec-more">${more(i)}</div></div>`).join("")}</div>`;
}
function openRec() { closeOmni(); const r = $("#rec"); r.classList.add("open"); $("#recBtn").setAttribute("aria-expanded", "true"); renderRecords(); enrichRuns(); }
function closeRec() { const r = $("#rec"); if (!r.classList.contains("open")) return; r.classList.remove("open"); $("#recBtn").setAttribute("aria-expanded", "false"); }
async function loadMeta(id) {
  if (!id || id === "sample" || (state.meta[id] && state.meta[id].loaded) || state.mode !== "live") return;
  try { rememberRun(await fetchJSON(`/runs/${encodeURIComponent(id)}`, 8000)); } catch (e) { state.meta[id] = { ...(state.meta[id] || {}), loaded: true }; }
  renderRecords();
}
// Fetch each run's question and recommendation (the list endpoint may only send ids and verdicts).
async function enrichRuns() {
  if (state.mode !== "live" || state.enriching) return;
  const todo = state.runs.filter(r => !state.meta[r.run_id]).slice(0, 25);
  if (!todo.length) return; state.enriching = true;
  let k = 0;
  const worker = async () => { while (k < todo.length) { const r = todo[k++]; try { rememberRun(await fetchJSON(`/runs/${encodeURIComponent(r.run_id)}`, 8000)); } catch (e) { state.meta[r.run_id] = { loaded: true }; } } };
  await Promise.all([worker(), worker(), worker(), worker()]);
  state.enriching = false; renderOmni(); renderRecords(); renderRecTile(); if (state.panel && state.panel.kind === "start") refreshPanel(true);
}
function rememberRun(run) {
  if (!run || !run.run_id) return; const vm = toViewModel(run);
  state.meta[run.run_id] = { question: vm.brief.question || "", decision_id: vm.brief.decision_id || "", stance: vm.memo && vm.memo.stance, verdict: vm.final && vm.final.verdict,
    rec: (vm.memo && vm.memo.recommendation) || "", conds: (vm.memo && vm.memo.conditions) || [], loaded: true };
}
async function openRunThen(id, sheet) { closeRec(); closeOmni(); await openRun(id); if (sheet) setTimeout(() => openSheet(sheet), 400); }
function fillPicker() { renderRecTile(); renderOmni(); renderRecords(); }
async function openRun(id, quiet) {
  clearInterval(state.poll); state.poll = null; clearInterval(clockTimer); clockTimer = null;
  state.runId = id; state.tok = (state.tok || 0) + 1;
  if (!quiet) { closePanel(); state.feed = []; }
  if (!id) { state.vm = toViewModel(IDLE_RUN); renderAll(state.vm); fillPicker(); return; }
  if (id === "sample" || state.mode !== "live") { state.vm = toViewModel(SAMPLE); state.runId = "sample"; renderAll(state.vm); fillPicker(); if (!quiet) entrance(); return; }
  try {
    const run = await fetchJSON(`/runs/${encodeURIComponent(id)}`, 8000);
    rememberRun(run); state.vm = toViewModel(run); renderAll(state.vm); fillPicker(); entrance();
    if (!["done", "failed"].includes(state.vm.status)) {
      clockTimer = setInterval(updateClock, 1000);
      let prev = state.vm.status;
      state.poll = setInterval(async () => {
        try {
          const nvm = toViewModel(await fetchJSON(`/runs/${encodeURIComponent(id)}`, 8000)); state.vm = nvm; renderAll(nvm);
          if (nvm.status !== prev) {
            const i = STAGES.findIndex(s => s.key === nvm.status);
            if (nvm.status === "moderating" || (i > 2 && prev === "advisers")) { $("#app").classList.add("scanning"); setTimeout(() => $("#app").classList.remove("scanning"), 1600); }
            if (i >= 0 && nvm.status !== "done") feed(`<span class="e-sig">${esc(STAGES[i].label)}</span> started`);
            prev = nvm.status;
          }
          if (["done", "failed"].includes(nvm.status)) {
            clearInterval(state.poll); clearInterval(clockTimer); loadRunList();
            if (nvm.status === "done") { burst(); feed(`<span class="e-ok">Verdict: ${esc(vsent(upper(nvm.final && nvm.final.verdict)))}</span>`); toast("Debate finished. The verdict is ready."); setTimeout(() => openPanel("verdict"), 900); }
            else toast("The run stopped. See the timeline.");
          }
        } catch (e) { console.warn("[colosseum] poll failed", e); }
      }, 3000);
    }
  } catch (e) { toast("Couldn't open that run (" + e.message + "). Showing the sample instead."); state.vm = toViewModel(SAMPLE); state.runId = "sample"; renderAll(state.vm); fillPicker(); }
}

let toastT;
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("on"); clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("on"), 3400); }

function wire() {
  document.addEventListener("click", e => {
    const ev = e.target.closest("[data-ev]"); if (ev) { e.preventDefault(); openPanel("evidence", ev.dataset.ev, !!state.panel); return; }
    const sh = e.target.closest("[data-sheet]"); if (sh && !sh.disabled) { e.preventDefault(); if (state.sheet === sh.dataset.sheet) closeSheet(); else openSheet(sh.dataset.sheet); return; }
    const orun = e.target.closest("[data-open-run]"); if (orun) { e.preventDefault(); closeOmni(); closeRec(); $("#histQ").value = ""; openRun(orun.dataset.openRun); return; }
    const rmore = e.target.closest("[data-rec-more]"); if (rmore) { e.preventDefault(); e.stopPropagation(); const id = rmore.dataset.recMore; state.recOpen = state.recOpen === id ? null : id; renderRecords(); loadMeta(id); return; }
    const rmemo = e.target.closest("[data-rec-memo]"); if (rmemo) { e.preventDefault(); openRunThen(rmemo.dataset.recMemo, "memo"); return; }
    const oa = e.target.closest("[data-open-adv]"); if (oa) { closeSheet(); openAdviser(oa.dataset.openAdv); }
  });
  $("#sideClose").addEventListener("click", closePanel); $("#sideBack").addEventListener("click", backPanel);
  $("#sheetClose").addEventListener("click", closeSheet); $("#scrim").addEventListener("click", closeSheet);
  document.addEventListener("keydown", e => { if (e.key === "Escape") { if (state.sheet) closeSheet(); else closePanel(); } });
  $("#replayBtn").addEventListener("click", replay);
  $("#newBtn").addEventListener("click", () => openSheet("new"));
  const q = $("#histQ");
  q.addEventListener("focus", openOmni);
  q.addEventListener("input", () => { state.histAct = 0; openOmni(); renderOmni(); });
  q.addEventListener("keydown", e => {
    if (e.key === "ArrowDown") { e.preventDefault(); openOmni(); moveHist(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); moveHist(-1); }
    else if (e.key === "Enter") { e.preventDefault(); const r = $$("#histList .hrow")[state.histAct]; if (r) { r.click(); q.blur(); } }
    else if (e.key === "Escape") { e.stopPropagation(); if (q.value) { q.value = ""; renderOmni(); } else { closeOmni(); q.blur(); } }
  });
  $("#recBtn").addEventListener("click", e => { e.stopPropagation(); $("#rec").classList.contains("open") ? closeRec() : openRec(); });
  document.addEventListener("click", e => { if (!e.target.isConnected) return; if (!e.target.closest("#omni")) closeOmni(); if (!e.target.closest("#rec")) closeRec(); });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && $("#rec").classList.contains("open")) { closeRec(); $("#recBtn").focus(); }
    if (e.key === "/" && !/INPUT|TEXTAREA|SELECT/.test((document.activeElement || {}).tagName || "")) { e.preventDefault(); q.focus(); }
  });
}

async function boot() {
  try { wire(); } catch (e) { console.error(e); }
  state.vm = toViewModel(SAMPLE); renderAll(state.vm); fillPicker();
  setTimeout(entrance, 300);
  const runs = await detectApi();
  if (runs) {
    state.mode = "live"; state.runs = runs; const m = $("#mode"); m.textContent = "Live"; m.classList.add("live");
    const pick = runs.find(r => String(r.status).toLowerCase() === "done") || runs[0];
    if (pick) await openRun(pick.run_id); else { state.runId = ""; await openRun(""); }
  }
}
boot();
})();
