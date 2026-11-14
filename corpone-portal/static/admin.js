"use strict";
const $ = s => document.querySelector(s);
let token = sessionStorage.getItem("adminToken") || "";
$("#token").value = token;

async function api(method, path, body) {
  const opts = { method, headers: { "X-Admin-Token": token } };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  const res = await fetch(path, opts);
  const data = res.status === 204 ? null : await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data && data.detail;
    throw new Error(typeof d === "string" ? d : (d || []).map(x => (x.msg || "").replace(/^Value error, /, "")).join("\n") || "Request failed");
  }
  return data;
}

function msg(id, text, kind = "err") { const el = $("#" + id); el.textContent = text; el.className = `msg ${kind}`; }
function td(t) { const c = document.createElement("td"); c.textContent = t ?? ""; return c; }
function row(cells) { const r = document.createElement("tr"); cells.forEach(c => r.append(c instanceof Node ? c : td(c))); return r; }
const fmt = o => Object.entries(o || {}).map(([k, v]) => `${k}: ${v}`).join(", ") || "-";

$("#saveToken").onclick = async () => {
  token = $("#token").value;
  sessionStorage.setItem("adminToken", token);
  try { await loadAll(); msg("msgToken", "Connected.", "ok"); } catch (e) { msg("msgToken", e.message); }
};

function reportText(r) {
  let t = `${r.accepted} accepted, ${r.paired} paired.`;
  if (r.paired_existing) t += ` ${r.paired_existing} older waiting item(s) were also paired.`;
  if (r.pairs.length) t += "\nPaired:\n" + r.pairs.slice(0, 50).map(p => `  SIM ${p.iccid} ↔ ${p.msisdn} (${p.sim_type})`).join("\n")
    + (r.paired > 50 ? `\n  …and ${r.paired - 50} more (see SIM ↔ number pairing)` : "");
  const waiting = r.items.filter(x => !(x.iccid && x.msisdn)).length;
  if (waiting) t += `\n${waiting} new item(s) still waiting to be paired.`;
  if (r.rejected_count) t += `\n${r.rejected_count} rejected:\n` + r.rejected.slice(0, 50).map(x => `  row ${x.row} (${x.value}): ${x.error}`).join("\n");
  return t;
}

for (const [form, path, out] of [["#simForm", "sims", "msgSim"], ["#numForm", "numbers", "msgNum"]]) {
  $(form).onsubmit = async e => {
    e.preventDefault();
    const fd = new FormData(e.target);
    for (const [k, v] of [...fd.entries()]) if (v === "") fd.delete(k);
    try {
      const r = await api("POST", `api/admin/inventory/${path}`, fd);
      msg(out, reportText(r), r.rejected_count ? "err" : "ok");
      e.target.reset(); loadInventory(); loadCategories();
      if (pair.category) loadPairings();
    } catch (err) { msg(out, err.message); }
  };
}

async function loadInventory() {
  const rows = await api("GET", "api/admin/inventory/summary");
  $("#invTable tbody").replaceChildren(...rows.map(c => row([c.category, fmt(c.sims.ESIM), fmt(c.sims.PHYSICAL), fmt(c.numbers), fmt(c.paired_available)])));
}
$("#refreshInv").onclick = loadInventory;

// ---- SIM <-> number pairing list, with the status of both sides
const PAGE = 100;
const pair = { category: "", q: "", filter: "", offset: 0 };
const STATUS = { AVAILABLE: ["Not paired", "PENDING"], PAIRED: ["Paired · ready", "COMPLETED"],
                 RESERVED: ["Reserved", "SUBMITTED"], ASSIGNED: ["Active line", "PROVISIONED"] };
function statusCell(status, waitingText) {
  const c = document.createElement("td");
  if (!status) { c.textContent = waitingText; c.style.color = "var(--muted)"; return c; }
  const [text, cls] = STATUS[status] || [status, ""];
  const p = document.createElement("span"); p.className = `pill ${cls}`; p.textContent = text; c.append(p);
  return c;
}
const mono = t => { const c = td(t ?? "–"); c.className = "mono"; return c; };
const when = iso => (iso ? iso.slice(0, 16).replace("T", " ") : "–");

async function loadPairings() {
  const params = new URLSearchParams({ category: pair.category, limit: PAGE, offset: pair.offset });
  if (pair.filter) params.set("status", pair.filter);
  if (pair.q) params.set("q", pair.q);
  let d;
  try { d = await api("GET", `api/admin/inventory/pairings?${params}`); }
  catch (err) { msg("msgPair", err.message); return; }
  $("#msgPair").className = "msg hidden";
  $("#pairTabs").classList.remove("hidden");
  const sims = ["AVAILABLE", "PAIRED", "RESERVED", "ASSIGNED"].reduce((a, k) => a + (d.counts[k] || 0), 0);
  document.querySelectorAll("#pairTabs button").forEach(b => {
    b.classList.toggle("on", b.dataset.f === pair.filter);
    b.querySelector("span").textContent = b.dataset.f ? (d.counts[b.dataset.f] ?? 0) : sims;
  });
  $("#pairTable tbody").replaceChildren(...(d.pairs.length ? d.pairs.map(x => {
    const r = row([mono(x.iccid), x.sim_type ? (x.sim_type === "ESIM" ? "eSIM" : "Physical") : "–",
      statusCell(x.sim_status, "No SIM yet"), mono(x.msisdn), statusCell(x.number_status, "No number yet"), when(x.paired_at)]);
    if (x.status_mismatch) { r.title = "SIM and number status differ"; r.style.background = "var(--err-soft)"; }
    return r;
  }) : [row([Object.assign(td("Nothing matches."), { colSpan: 6 })])]));
  const end = Math.min(d.offset + d.pairs.length, d.total);
  $("#pairShowing").textContent = d.total ? `${d.offset + 1}–${end} of ${d.total}` : "";
  $("#pairPrev").classList.toggle("hidden", d.offset === 0);
  $("#pairNext").classList.toggle("hidden", end >= d.total);
}
$("#pairForm").onsubmit = e => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target));
  Object.assign(pair, { category: f.category.trim().toUpperCase(), q: f.q.trim(), offset: 0 });
  loadPairings();
};
document.querySelectorAll("#pairTabs button").forEach(b => b.onclick = () => { pair.filter = b.dataset.f; pair.offset = 0; loadPairings(); });
$("#pairPrev").onclick = () => { pair.offset = Math.max(0, pair.offset - PAGE); loadPairings(); };
$("#pairNext").onclick = () => { pair.offset += PAGE; loadPairings(); };

// ---- SIM category allocation (category -> company UEN)
$("#catForm").onsubmit = async e => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target));
  try {
    const a = await api("PUT", "api/admin/categories", { ...f, active: true });
    msg("msgCat", a.uen ? `${a.category} is allocated to UEN ${a.uen} only.` : `${a.category} is available to all companies.`, "ok");
    e.target.reset(); loadCategories();
  } catch (err) { msg("msgCat", err.message); }
};

async function loadCategories() {
  const rows = await api("GET", "api/admin/categories");
  $("#catTable tbody").replaceChildren(...(rows.length ? rows.map(a => {
    const b = document.createElement("button");
    b.className = "link"; b.textContent = a.active ? "Deactivate" : "Reactivate";
    b.onclick = async () => {
      const who = a.uen ? a.uen : "all companies";
      if (a.active && !confirm(`Hide ${a.category} from ${who}? Draft sign-ups using it must choose another category.`)) return;
      try { await api("PUT", "api/admin/categories", { category: a.category, uen: a.uen, description: a.description, active: !a.active }); loadCategories(); }
      catch (err) { msg("msgCat", err.message); }
    };
    const cell = document.createElement("td"); cell.append(b);
    const scope = a.uen ? `UEN ${a.uen}` : a.allocated ? "All companies" : "All companies (not allocated)";
    const status = a.active ? "active" : "inactive (hidden)";
    return row([a.category, scope, a.description || "–", a.ESIM, a.PHYSICAL, status, cell]);
  }) : [row([Object.assign(td("No SIM categories yet. Allocate one above, or upload SIMs/numbers with a category."), { colSpan: 7 })])]));
}

// ---- Delegated merchant codes (shown in the AO step's drop-down when active)
$("#merForm").onsubmit = async e => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target));
  try {
    const m = await api("PUT", "api/admin/merchants", { ...f, active: true });
    msg("msgMer", `${m.code} saved and active.`, "ok"); e.target.reset(); loadMerchants();
  } catch (err) { msg("msgMer", err.message); }
};
async function loadMerchants() {
  const rows = await api("GET", "api/admin/merchants");
  $("#merTable tbody").replaceChildren(...rows.map(m => {
    const b = document.createElement("button");
    b.className = "link"; b.textContent = m.active ? "Deactivate" : "Reactivate";
    b.onclick = async () => {
      try { await api("PUT", "api/admin/merchants", { ...m, active: !m.active }); loadMerchants(); }
      catch (err) { msg("msgMer", err.message); }
    };
    const cell = document.createElement("td"); cell.append(b);
    return row([m.code, m.name, m.active ? "active" : "inactive", cell]);
  }));
}

$("#blSubject").onchange = e => {
  $("#blIdType").innerHTML = e.target.value === "COMPANY" ? "<option>UEN</option>" : "<option>NRIC</option><option>FIN</option><option>PASSPORT</option>";
};

$("#blForm").onsubmit = async e => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target));
  if (!f.end_date) f.end_date = null;
  try { await api("POST", "api/admin/blacklist", f); msg("msgBl", "Added.", "ok"); e.target.reset(); loadBlacklist(); }
  catch (err) { msg("msgBl", err.message); }
};

async function loadBlacklist() {
  const rows = await api("GET", "api/admin/blacklist");
  $("#blTable tbody").replaceChildren(...rows.map(b => {
    const btn = document.createElement("button");
    btn.className = "link"; btn.textContent = "Remove";
    btn.onclick = async () => { if (confirm("Remove this blacklist entry?")) { await api("DELETE", `api/admin/blacklist/${b.id}`); loadBlacklist(); } };
    const cell = document.createElement("td"); cell.append(btn);
    return row([b.subject_type === "COMPANY" ? "Company" : "Account officer", `${b.id_type} ${b.id_value}`, b.start_date, b.end_date || "indefinite", b.reason, cell]);
  }));
}

// ---- API gateway clients
async function loadScopes() {
  const scopes = await api("GET", "api/admin/gateway/scopes");
  $("#scopeBoxes").replaceChildren(...Object.entries(scopes).map(([k, v]) => {
    const l = document.createElement("label");
    l.style.cssText = "display:flex;gap:6px;align-items:center;color:inherit";
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.name = "scopes"; cb.value = k; cb.style.width = "auto";
    l.append(cb, document.createTextNode(`${k}: ${v}`));
    return l;
  }));
}

$("#keyForm").onsubmit = async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = { name: fd.get("name"), scopes: fd.getAll("scopes") };
  if (fd.get("rate_limit_per_min")) body.rate_limit_per_min = +fd.get("rate_limit_per_min");
  try {
    const r = await api("POST", "api/admin/gateway/clients", body);
    msg("msgKey", `API key for "${r.name}" (shown once, copy it now):\n${r.api_key}`, "ok");
    e.target.reset(); loadKeys();
  } catch (err) { msg("msgKey", err.message); }
};

async function loadKeys() {
  const rows = await api("GET", "api/admin/gateway/clients");
  $("#keyTable tbody").replaceChildren(...rows.map(c => {
    const cell = document.createElement("td");
    if (c.active) {
      const b = document.createElement("button");
      b.className = "link"; b.textContent = "Revoke";
      b.onclick = async () => { if (confirm(`Revoke key for ${c.name}?`)) { await api("DELETE", `api/admin/gateway/clients/${c.id}`); loadKeys(); } };
      cell.append(b);
    }
    return row([c.name, c.key_prefix, c.scopes.join(" "), c.rate_limit_per_min, c.last_used_at || "never", c.active ? "active" : "revoked", cell]);
  }));
}

// ---- Gateway monitoring
async function loadLogs() {
  const [out, audit] = await Promise.all([api("GET", "api/admin/gateway/outbound?limit=50"), api("GET", "api/admin/gateway/audit?limit=50")]);
  $("#upTable tbody").replaceChildren(...Object.entries(out.upstreams).map(([n, u]) => {
    const st = document.createElement("td"), pill = document.createElement("span");
    pill.className = `pill ${u.circuit === "closed" ? "COMPLETED" : "FAILED"}`; pill.textContent = u.circuit; st.append(pill);
    return row([n, st, u.consecutive_failures]);
  }));
  $("#outTable tbody").replaceChildren(...out.calls.map(c => row([c.at.slice(0, 19), c.upstream, c.operation, c.attempt, c.outcome, c.latency_ms, c.error || ""])));
  $("#auditTable tbody").replaceChildren(...audit.map(a => row([a.at.slice(0, 19), a.client, `${a.method} ${a.path}`, a.status, a.latency_ms, a.ip])));
}
$("#refreshLogs").onclick = loadLogs;
$("#resetBreakers").onclick = async () => { await api("POST", "api/admin/gateway/outbound/reset"); loadLogs(); };

async function loadAll() { await Promise.all([loadInventory(), loadCategories(), loadMerchants(), loadBlacklist(), loadScopes(), loadKeys(), loadLogs()]); }
if (token) loadAll().catch(e => msg("msgToken", e.message));
