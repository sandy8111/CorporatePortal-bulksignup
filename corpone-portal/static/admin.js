"use strict";
const $ = s => document.querySelector(s);
let token = sessionStorage.getItem("adminToken") || "";
$("#token").value = token;

async function api(method, path, body) {
  const opts = { method, headers: { "X-Admin-Token": token } };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  opts.cache = "no-store";  // never reuse a cached API response
  if (method === "GET") path += (path.includes("?") ? "&" : "?") + "_ts=" + Date.now();  // skip copies a CDN already holds
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

function reportText(r, kind) {
  const name = r.filename ? r.filename + ": " : "";
  let t;
  if (kind === "numbers") {
    t = `${name}${r.accepted} number(s) added as available. They pair when SIMs are uploaded to the same category.`;
  } else {
    t = `${name}${r.accepted} accepted, ${r.paired} paired with an available number.`;
    if (r.paired_existing) t += ` ${r.paired_existing} SIM(s) uploaded earlier were also paired with numbers left over.`;
    if (r.pairs.length) t += "\nPaired:\n" + r.pairs.slice(0, 50).map(p => `  SIM ${p.iccid} ↔ ${p.msisdn} (${p.sim_type})`).join("\n")
      + (r.paired > 50 ? `\n  …and ${r.paired - 50} more (see Inventory lists)` : "");
    const waiting = r.items.filter(x => !x.msisdn).length;
    if (waiting) t += `\n${waiting} SIM(s) are waiting: no available numbers left in the category.`;
  }
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
      msg(out, reportText(r, path), r.rejected_count ? "err" : "ok");
      e.target.reset(); loadInventory(); loadCategories(); loadUploads(r.upload_id);
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

// ---- Orders & carts: sign-ups with lines in the cart (DRAFT) and submitted orders
const ORD_PAGE = 50;
const ord = { status: "", q: "", offset: 0, timer: null };
const ORDER_STATUS = { DRAFT: ["In cart", "PENDING"], SUBMITTED: ["Submitted", "SUBMITTED"], COMPLETED: ["Completed", "COMPLETED"],
                       PARTIALLY_FAILED: ["Partly failed", "PARTIALLY_FAILED"], FAILED: ["Failed", "FAILED"] };
const LINE_STATUS = { PENDING: ["Pending", "PENDING"], PROVISIONED: ["Provisioned", "PROVISIONED"], FAILED: ["Failed", "FAILED"] };
const money = v => "$" + Number(v).toFixed(2);
function pillCell(map, st) {
  const c = document.createElement("td");
  if (!st) { c.textContent = "–"; return c; }
  const [text, cls] = map[st] || [st, ""];
  const p = document.createElement("span"); p.className = "pill " + cls; p.textContent = text; c.append(p);
  return c;
}

async function loadOrders() {
  const q = new URLSearchParams({ limit: ORD_PAGE, offset: ord.offset });
  if (ord.status) q.set("status", ord.status);
  if (ord.q) q.set("q", ord.q);
  let d;
  try { d = await api("GET", "api/admin/orders?" + q); } catch (err) { return msg("msgOrd", err.message); }
  $("#msgOrd").className = "msg hidden";
  const all = Object.values(d.counts).reduce((a, b) => a + b, 0);
  document.querySelectorAll("#ordTabs button").forEach(b => {
    b.classList.toggle("on", b.dataset.s === ord.status);
    b.querySelector("span").textContent = b.dataset.s ? (d.counts[b.dataset.s] ?? 0) : all;
  });
  $("#ordTable tbody").replaceChildren(...(d.orders.length ? d.orders.map(o => {
    const b = document.createElement("button");
    b.className = "link"; b.textContent = "View"; b.onclick = () => openOrder(o.id);
    const cell = document.createElement("td"); cell.append(b);
    const mix = [o.esim ? o.esim + " eSIM" : "", o.physical ? o.physical + " physical" : ""].filter(Boolean).join(", ");
    return row([when(o.created_at), o.company_name + " · " + o.uen, o.ao_email, o.category || "–",
                o.line_count + " (" + mix + ")", money(o.monthly_total), pillCell(ORDER_STATUS, o.status),
                mono(o.account_number), cell]);
  }) : [row([Object.assign(td(ord.q || ord.status ? "No orders match." : "No lines have been added to a cart yet."), { colSpan: 9 })])]));
  const end = ord.offset + d.orders.length;
  $("#ordShowing").textContent = d.total ? (ord.offset + 1) + "–" + end + " of " + d.total : "";
  $("#ordPrev").classList.toggle("hidden", ord.offset === 0);
  $("#ordNext").classList.toggle("hidden", end >= d.total);
}

async function openOrder(id) {
  let o;
  try { o = await api("GET", "api/admin/orders/" + encodeURIComponent(id)); } catch (err) { return msg("msgOrd", err.message); }
  $("#ordDetail").classList.remove("hidden");
  $("#ordTitle").textContent = o.company_name + " · " + o.uen;
  $("#ordMeta").textContent = [
    ORDER_STATUS[o.status] ? ORDER_STATUS[o.status][0] : o.status,
    o.account_number ? "account " + o.account_number : "not submitted yet",
    "AO " + [o.ao_name, o.ao_email].filter(Boolean).join(" "),
    o.merchant_code ? "merchant " + o.merchant_code : null,
    "SIM category " + (o.category || "–"),
    o.line_count + " line(s), " + money(o.monthly_total) + "/month",
    "created " + when(o.created_at),
    o.submitted_at ? "submitted " + when(o.submitted_at) : null,
  ].filter(Boolean).join(" · ");
  $("#ordLines tbody").replaceChildren(...o.lines.map((l, i) => {
    const note = l.error || (l.sim_type === "ESIM" && l.line_status ? (l.esim_qr_sent ? "eSIM QR emailed" : "QR not sent yet") : "");
    return row([i + 1, mono(l.msisdn), mono(l.iccid), simTypeText(l.sim_type), l.plan, money(l.monthly_price),
                statusCell(l.inventory_status, "–"), pillCell(LINE_STATUS, l.line_status), note]);
  }));
  $("#ordDetail").scrollIntoView({ behavior: "smooth", block: "nearest" });
}
document.querySelectorAll("#ordTabs button").forEach(b => b.onclick = () => { ord.status = b.dataset.s; ord.offset = 0; loadOrders(); });
$("#ordSearch").oninput = e => { clearTimeout(ord.timer); ord.timer = setTimeout(() => { ord.q = e.target.value.trim(); ord.offset = 0; loadOrders(); }, 250); };
$("#ordPrev").onclick = () => { ord.offset = Math.max(0, ord.offset - ORD_PAGE); loadOrders(); };
$("#ordNext").onclick = () => { ord.offset += ORD_PAGE; loadOrders(); };
$("#ordClose").onclick = () => $("#ordDetail").classList.add("hidden");

// ---- Inventory lists: SIMs by category / type / status, numbers by category / status
const LIST_PAGE = 100;
const list = { kind: null, params: {}, offset: 0 };
const STATUS_LABEL = { AVAILABLE: "Not paired", PAIRED: "Paired · ready", RESERVED: "Reserved in a cart", ASSIGNED: "Active line" };
const simTypeText = t => (t ? (t === "ESIM" ? "eSIM" : "Physical") : "–");

function fillCategoryPickers(cats) {
  document.querySelectorAll(".catPick").forEach(sel => {
    const keep = sel.value;
    sel.replaceChildren(Object.assign(document.createElement("option"), { value: "", textContent: "All categories" }),
      ...cats.map(c => Object.assign(document.createElement("option"), { value: c, textContent: c })));
    if (cats.includes(keep)) sel.value = keep;
  });
}

async function runList() {
  const q = new URLSearchParams({ limit: LIST_PAGE, offset: list.offset });
  for (const [k, v] of Object.entries(list.params)) if (v) q.set(k, v);
  const sims = list.kind === "sims";
  let d;
  try { d = await api("GET", `api/admin/inventory/${sims ? "sim-list" : "number-list"}?${q}`); }
  catch (err) { return msg("msgList", err.message); }
  $("#msgList").className = "msg hidden";
  $("#listResult").classList.remove("hidden");
  const p = list.params;
  const filters = [sims ? p.category || "all categories" : null, sims && p.sim_type ? simTypeText(p.sim_type) : null,
                   p.status ? STATUS_LABEL[p.status] : "all statuses", p.q ? `matching "${p.q}"` : null].filter(Boolean);
  $("#listTitle").textContent = `${sims ? "SIMs" : "Numbers"} · ${filters.join(" · ")}`;
  const items = sims ? d.sims : d.numbers;
  $("#listShowing").textContent = d.total ? `${list.offset + 1}–${list.offset + items.length} of ${d.total}` : "0 found";
  // status counts for the same filters; click one to narrow the list
  $("#listCounts").replaceChildren(...Object.entries(d.counts).map(([st, n]) => {
    const c = document.createElement("button");
    c.type = "button"; c.className = `chip ${p.status === st ? "info" : ""}`; c.style.border = "0"; c.style.cursor = "pointer";
    c.textContent = `${STATUS_LABEL[st]}: ${n}`;
    c.onclick = () => { list.params.status = p.status === st ? "" : st; list.offset = 0;
      $(sims ? "#simListForm" : "#numListForm").elements.status.value = list.params.status; runList(); };
    return c;
  }));
  const head = sims ? ["ICCID", "Type", "Category", "SIM status", "Paired number", "Number status", "Uploaded", "Paired at"]
                    : ["Mobile number", "Number status", "Paired SIM", "SIM type", "SIM status", "Uploaded"];
  $("#listTable thead").replaceChildren(row(head.map(h => Object.assign(document.createElement("th"), { textContent: h }))));
  $("#listTable tbody").replaceChildren(...(items.length ? items.map(x => sims
    ? row([mono(x.iccid), simTypeText(x.sim_type), x.category, statusCell(x.status, "–"), mono(x.msisdn),
           statusCell(x.number_status, "No number yet"), when(x.uploaded_at), when(x.paired_at)])
    : row([mono(x.msisdn), statusCell(x.status, "–"), mono(x.iccid), simTypeText(x.sim_type),
           statusCell(x.sim_status, "No SIM yet"), when(x.uploaded_at)]))
    : [row([Object.assign(td("Nothing matches these filters."), { colSpan: head.length })])]));
  $("#listPrev").classList.toggle("hidden", list.offset === 0);
  $("#listNext").classList.toggle("hidden", list.offset + items.length >= d.total);
}
for (const [form, kind] of [["#simListForm", "sims"], ["#numListForm", "numbers"]]) {
  $(form).onsubmit = e => {
    e.preventDefault();
    Object.assign(list, { kind, params: Object.fromEntries(new FormData(e.target)), offset: 0 });
    list.params.q = (list.params.q || "").trim();
    runList();
  };
}
$("#listPrev").onclick = () => { list.offset = Math.max(0, list.offset - LIST_PAGE); runList(); };
$("#listNext").onclick = () => { list.offset += LIST_PAGE; runList(); };

// ---- Uploaded files and their rows
const ups = { kind: "", open: null, result: "", offset: 0 };
const ROWS_PAGE = 200;
const FROM = { BACK_OFFICE: "Back office", PORTAL: "Portal", GATEWAY: "API gateway" };
const STATUS_TEXT = { AVAILABLE: "Not paired", PAIRED: "Paired · ready", RESERVED: "Reserved in a cart", ASSIGNED: "Active line" };

async function loadUploads(openId) {
  const q = new URLSearchParams({ limit: 50 });
  if (ups.kind) q.set("kind", ups.kind);
  let d;
  try { d = await api("GET", `api/admin/uploads?${q}`); } catch (err) { return msg("msgSim", err.message); }
  $("#upShowing").textContent = d.total > d.uploads.length ? `Newest ${d.uploads.length} of ${d.total}` : `${d.total} file(s)`;
  $("#upList tbody").replaceChildren(...(d.uploads.length ? d.uploads.map(u => {
    const b = document.createElement("button");
    b.className = "link"; b.textContent = "View";
    b.onclick = () => openUpload(u.id);
    const cell = document.createElement("td"); cell.append(b);
    const name = td(u.filename || "(no name)"); name.className = "mono";
    const r = row([when(u.uploaded_at), name, u.kind === "SIMS" ? "SIMs" : "Numbers",
      `${FROM[u.source] || u.source}${u.uploaded_by && u.uploaded_by !== "admin" ? " · " + u.uploaded_by : ""}`,
      u.default_category || "from file", u.total_rows, u.accepted, u.paired, u.rejected, cell]);
    return r;
  }) : [row([Object.assign(td("No files uploaded yet."), { colSpan: 10 })])]));
  if (openId) openUpload(openId);
}

async function openUpload(id, keepFilter = false) {
  if (!keepFilter || ups.open !== id) Object.assign(ups, { result: "", offset: 0 });
  ups.open = id;
  const q = new URLSearchParams({ limit: ROWS_PAGE, offset: ups.offset });
  if (ups.result) q.set("result", ups.result);
  let d;
  try { d = await api("GET", `api/admin/uploads/${id}?${q}`); } catch (err) { return msg("msgSim", err.message); }
  const sims = d.kind === "SIMS";
  $("#upDetail").classList.remove("hidden");
  $("#upDetailTitle").textContent = d.filename || "(no name)";
  $("#upDetailMeta").textContent = `${sims ? "SIM" : "Number"} file · uploaded ${when(d.uploaded_at)} from ${FROM[d.source] || d.source}` +
    `${d.uploaded_by ? " by " + d.uploaded_by : ""} · ${d.total_rows} rows: ${d.accepted} accepted, ${d.paired} paired, ${d.rejected} rejected`;
  $("#upValueHead").textContent = sims ? "ICCID" : "Mobile number";
  $("#upPairHead").textContent = sims ? "Paired number" : "Paired SIM";
  document.querySelectorAll("#upRowTabs button").forEach(b => b.classList.toggle("on", b.dataset.r === ups.result));
  $("#upRows tbody").replaceChildren(...(d.rows.length ? d.rows.map(x => {
    const ok = x.result === "ACCEPTED";
    const res = document.createElement("td"), p = document.createElement("span");
    p.className = `pill ${ok ? "COMPLETED" : "FAILED"}`; p.textContent = ok ? "Accepted" : "Rejected"; res.append(p);
    const status = ok ? (STATUS_TEXT[x.status] || x.status || "no longer in inventory") : x.error;
    return row([x.row, mono(x.value), res, mono(x.paired_with), status]);
  }) : [row([Object.assign(td("No rows."), { colSpan: 5 })])]));
  const end = d.offset + d.rows.length;
  $("#upRowShowing").textContent = d.rows_total ? `Rows ${d.offset + 1}–${end} of ${d.rows_total}` : "";
  $("#upPrev").classList.toggle("hidden", d.offset === 0);
  $("#upNext").classList.toggle("hidden", end >= d.rows_total);
}
document.querySelectorAll("#upTabs button").forEach(b => b.onclick = () => {
  ups.kind = b.dataset.k;
  document.querySelectorAll("#upTabs button").forEach(x => x.classList.toggle("on", x === b));
  loadUploads();
});
document.querySelectorAll("#upRowTabs button").forEach(b => b.onclick = () => { ups.result = b.dataset.r; ups.offset = 0; openUpload(ups.open, true); });
$("#upPrev").onclick = () => { ups.offset = Math.max(0, ups.offset - ROWS_PAGE); openUpload(ups.open, true); };
$("#upNext").onclick = () => { ups.offset += ROWS_PAGE; openUpload(ups.open, true); };
$("#upDetailClose").onclick = () => { $("#upDetail").classList.add("hidden"); ups.open = null; };

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
  fillCategoryPickers(rows.map(r => r.category).sort());
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

async function loadAll() { await Promise.all([loadInventory(), loadOrders(), loadUploads(), loadCategories(), loadMerchants(), loadBlacklist(), loadScopes(), loadKeys(), loadLogs()]); }
if (token) loadAll().catch(e => msg("msgToken", e.message));
