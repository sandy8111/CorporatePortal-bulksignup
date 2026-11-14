"use strict";
// All URLs are relative so the portal works behind a path prefix (e.g. /ports/8077/).
const $ = (s, el = document) => el.querySelector(s);
const TOTAL_STEPS = 6;
const state = { signup: null, plan: null, email: null, otpEmail: null, step: 1, maxStep: 1, submitted: false };

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  const res = await fetch(path, opts);
  const data = res.status === 204 ? null : await res.json().catch(() => ({}));
  if (res.status === 401 && !path.startsWith("api/auth/")) { signedOut(); throw new Error("Please sign in again"); }
  if (!res.ok) throw new Error(errText(data));
  return data;
}

function errText(data) {
  if (!data || !data.detail) return "Request failed";
  if (typeof data.detail === "string") return data.detail;
  return data.detail.map(d => `${(d.loc || []).slice(-1)[0]}: ${(d.msg || "").replace(/^Value error, /, "")}`).join("\n");
}

function msg(id, text, kind = "err") {
  const el = $("#" + id);
  el.textContent = text || ""; el.className = `msg ${kind}`;
  if (!text) el.classList.add("hidden");
}

function td(text) { const c = document.createElement("td"); c.textContent = text ?? ""; return c; }
function row(cells) { const r = document.createElement("tr"); cells.forEach(c => r.append(c instanceof Node ? c : td(c))); return r; }
function dl(el, pairs) {
  el.replaceChildren(...pairs.flatMap(([k, v]) => {
    const dt = document.createElement("dt"), dd = document.createElement("dd");
    dt.textContent = k; dd.textContent = v ?? "–"; return [dt, dd];
  }));
}
const money = v => `$${Number(v).toFixed(2)}`;

// ---------------------------------------------------------------- sequence navigation
function go(step) {
  state.step = step;
  state.maxStep = Math.max(state.maxStep, step);
  document.querySelectorAll("section.step").forEach(s => s.classList.toggle("hidden", +s.dataset.step !== step));
  document.querySelectorAll("#steps li").forEach(li => {
    const n = +li.dataset.step;
    li.className = n < step || (state.submitted && n <= 6 && n !== step) ? "done" : n === step ? "active" : "upcoming";
    // Completed steps 2-5 are clickable until the order is submitted.
    const canJump = !state.submitted && n >= 2 && n < step;
    li.classList.toggle("clickable", canJump);
    li.onclick = canJump ? () => go(n) : null;
  });
  const pct = state.submitted ? 100 : ((step - 1) / (TOTAL_STEPS - 1)) * 100;
  $("#progressBar").style.width = `${pct}%`;
  $("#progressLabel").textContent = state.submitted ? "Order submitted" : `Step ${step} of ${TOTAL_STEPS}`;
  if (step === 4) loadStep4();
  if (step === 5) renderReview();
  window.scrollTo({ top: 0, behavior: "smooth" });
}
document.addEventListener("click", e => {
  const b = e.target.closest("[data-back]");
  if (b && !state.submitted) { e.preventDefault(); go(+b.dataset.back); }
});

// ---------------------------------------------------------------- step 1: sign in (email OTP)
function signedOut() {
  state.email = null;
  $("#whoami").classList.add("hidden"); $("#logout").classList.add("hidden"); $("#boLink").classList.remove("hidden");
  state.maxStep = 1; go(1);
}

async function requestCode(email) {
  await api("POST", "api/auth/otp/request", { email });
  state.otpEmail = email;
  $("#otpEmail").textContent = email;
  $("#otpRequestForm").classList.add("hidden");
  $("#otpVerifyForm").classList.remove("hidden");
  $("#otpVerifyForm").elements.code.focus();
  msg("msg1", "Code sent. Check your inbox.", "ok");
}

$("#otpRequestForm").onsubmit = async e => {
  e.preventDefault();
  try { await requestCode(e.target.elements.email.value.trim()); } catch (err) { msg("msg1", err.message); }
};
$("#otpResend").onclick = async () => { try { await requestCode(state.otpEmail); } catch (err) { msg("msg1", err.message); } };
$("#otpBack").onclick = () => {
  $("#otpVerifyForm").classList.add("hidden"); $("#otpRequestForm").classList.remove("hidden"); msg("msg1", "");
};
$("#otpVerifyForm").onsubmit = async e => {
  e.preventDefault();
  try {
    await api("POST", "api/auth/otp/verify", { email: state.otpEmail, code: e.target.elements.code.value });
    msg("msg1", ""); e.target.reset();
    $("#otpVerifyForm").classList.add("hidden"); $("#otpRequestForm").classList.remove("hidden");
    await signedIn();
  } catch (err) { msg("msg1", err.message); }
};
$("#logout").onclick = async e => {
  e.preventDefault();
  await api("POST", "api/auth/logout").catch(() => {});
  sessionStorage.removeItem("signupId");
  location.reload();
};

async function signedIn() {
  const me = await api("GET", "api/auth/me");
  state.email = me.email;
  $("#whoEmail").textContent = me.email;
  $("#whoInitial").textContent = me.email[0].toUpperCase();
  $("#whoami").classList.remove("hidden"); $("#logout").classList.remove("hidden"); $("#boLink").classList.add("hidden");
  $("#aoForm").elements.email.value = me.email;
  await resume();
}

// ---------------------------------------------------------------- step 2: company + contract
function lockCompanyForm(s) {
  const f = $("#companyForm");
  for (const k of ["uen", "company_name", "category", "company_address"]) f.elements[k].value = s[k] || "";
  f.querySelectorAll("input,textarea,button").forEach(i => i.disabled = true);
  $("#subCompany").classList.add("complete");
  $("#contractForm").querySelectorAll("input,button").forEach(i => i.disabled = false);
}

$("#companyForm").onsubmit = async e => {
  e.preventDefault();
  try {
    state.signup = await api("POST", "api/signups", Object.fromEntries(new FormData(e.target)));
    sessionStorage.setItem("signupId", state.signup.id);
    lockCompanyForm(state.signup);
    msg("msg2", `UEN ${state.signup.uen} is valid and not blacklisted. Now upload the signed contract.`, "ok");
  } catch (err) { msg("msg2", err.message); }
};

$("#contractForm").onsubmit = async e => {
  e.preventDefault();
  try {
    const r = await api("POST", `api/signups/${state.signup.id}/contract`, new FormData(e.target));
    state.signup.contract_uploaded = true;
    state.signup.contract_filename = r.contract_filename;
    $("#subContract").classList.add("complete");
    msg("msg2", `Contract "${r.contract_filename}" uploaded securely.${r.ai_review ? " An AI check of the contract is running in the background." : ""}`, "ok");
    $("#toStep3").disabled = false;
  } catch (err) { msg("msg2", err.message); }
};
$("#toStep3").onclick = () => go(3);

// ---------------------------------------------------------------- step 3: account officer
$("#aoForm").onsubmit = async e => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target));
  delete f.email;  // server uses the verified login email
  try {
    state.signup = await api("PUT", `api/signups/${state.signup.id}/account-officer`, f);
    msg("msg3", "");
    go(4);
  } catch (err) { msg("msg3", err.message); }
};

// ---------------------------------------------------------------- step 4: plan, SIM upload, cart
async function loadStep4() {
  if (!$("#plans").children.length) {
    const plans = await api("GET", "api/plans");
    for (const p of plans) {
      const card = document.createElement("div");
      card.className = "plan";
      card.innerHTML = `<div class="name"></div><div class="price"></div><div class="data"></div><div class="desc"></div>`;
      $(".name", card).textContent = p.name.replace(/^\$\d+\s*/, "");
      $(".price", card).innerHTML = `$${Number(p.monthly_price).toFixed(0)}<small>/month per line</small>`;
      $(".data", card).textContent = `${p.data_gb} GB data`;
      $(".desc", card).textContent = p.description;
      card.onclick = () => {
        state.plan = p.code;
        document.querySelectorAll(".plan").forEach(c => c.classList.toggle("selected", c === card));
        $("#subPlan").classList.add("complete");
        msg("msg4", "");
      };
      $("#plans").append(card);
    }
  }
  refreshAvailability();
  renderCartBar();
  loadInventory();
}

async function refreshAvailability() {
  const a = await api("GET", `api/signups/${state.signup.id}/availability`);
  $("#availEsim").textContent = a.ESIM; $("#availPhys").textContent = a.PHYSICAL; $("#availCat").textContent = a.category;
}

const simType = t => (t === "ESIM" ? "eSIM" : "Physical");
function pill(text, cls) { const c = document.createElement("td"), p = document.createElement("span"); p.className = `pill ${cls}`; p.textContent = text; c.append(p); return c; }
function mono(text) { const c = td(text); c.className = "mono"; return c; }
const STATUS = { AVAILABLE: ["Not paired", "wait"], PAIRED: ["Paired · ready", "ready"],
                 RESERVED: ["Reserved", "reserved"], ASSIGNED: ["Active line", "active"] };
function statusPill(status, mine = false) {
  if (!status) return td("–");
  if (mine) return pill("In your cart", "mine");
  const [text, cls] = STATUS[status] || [status, "wait"];
  return pill(text, cls);
}
function chip(text, cls) { const c = document.createElement("span"); c.className = `chip ${cls}`; c.textContent = text; return c; }

// ---- SIM upload result: new (paired / waiting), already uploaded (with its pairing), rejected
$("#simForm").onsubmit = async e => {
  e.preventDefault();
  try {
    const r = await api("POST", `api/signups/${state.signup.id}/sims`, new FormData(e.target));
    const dups = r.rejected.filter(x => x.code === "already_uploaded");
    const bad = r.rejected.filter(x => x.code !== "already_uploaded");
    const waiting = r.items.filter(x => !x.msisdn).length;
    msg("msgSim", "");
    $("#simResultChips").replaceChildren(
      chip(`${r.accepted} new SIM(s) added`, r.accepted ? "ok" : ""),
      chip(`${r.paired} paired with a number`, r.paired ? "info" : ""),
      ...(waiting ? [chip(`${waiting} waiting for a number`, "warn")] : []),
      ...(dups.length ? [chip(`${dups.length} already uploaded`, "warn")] : []),
      ...(bad.length ? [chip(`${bad.length} rejected`, "err")] : []));
    const rows = [
      ...r.items.map(x => row(["", mono(x.iccid), simType(x.sim_type),
        x.msisdn ? pill("New · paired", "ready") : pill("New · waiting for number", "wait"), mono(x.msisdn || "–")])),
      ...dups.map(x => row([x.row, mono(x.value), x.existing ? simType(x.existing.sim_type) : "–",
        pill(x.error, "dup"), mono(x.existing && x.existing.msisdn || "–")])),
      ...bad.map(x => row([x.row, mono(x.value), "–", pill(x.error, "bad"), "–"])),
    ];
    $("#simResultTable tbody").replaceChildren(...rows);
    $("#simResult").classList.toggle("hidden", !rows.length);
    if (r.accepted) $("#subSim").classList.add("complete");
    e.target.elements.file.value = "";
    refreshAvailability();
    loadInventory();
  } catch (err) { msg("msgSim", err.message); $("#simResult").classList.add("hidden"); }
};

// ---- Add to cart: show exactly which SIM <-> number lines were added
$("#cartForm").onsubmit = async e => {
  e.preventDefault();
  if (!state.plan) return msg("msg4", "Select a plan above first.");
  const f = Object.fromEntries(new FormData(e.target));
  try {
    const cart = await api("POST", `api/signups/${state.signup.id}/cart`,
      { plan_code: state.plan, sim_type: f.sim_type, quantity: +f.quantity });
    state.signup.cart = cart;
    const added = cart.added || [];
    const preview = added.slice(0, 5).map(l => `${l.msisdn} ↔ SIM ${l.iccid}`).join("\n");
    msg("msg4", `Added ${added.length} ${simType(f.sim_type)} line(s) on ${added[0] ? added[0].plan_name : "the selected plan"}:\n` +
      preview + (added.length > 5 ? `\n…and ${added.length - 5} more (see the cart below)` : ""), "ok");
    renderCartBar(new Set(added.map(l => l.id)));
    refreshAvailability();
    loadInventory();
  } catch (err) { msg("msg4", err.message); }
};

function renderCartBar(highlight = new Set()) {
  const c = state.signup.cart;
  $("#cartCount").textContent = `${c.line_count} line${c.line_count === 1 ? "" : "s"}`;
  $("#cartTotal").textContent = money(c.monthly_total);
  $("#toStep5").disabled = !c.line_count;
  $("#subCart").classList.toggle("complete", c.line_count > 0);
  $("#cartBox").classList.toggle("hidden", !c.line_count);
  $("#cartTable tbody").replaceChildren(...c.lines.map((l, i) => {
    const b = document.createElement("button");
    b.className = "link"; b.textContent = "Remove";
    b.onclick = async () => {
      state.signup.cart = await api("DELETE", `api/signups/${state.signup.id}/cart/${l.id}`);
      msg("msg4", `Removed ${l.msisdn}. The SIM and number stay paired and are free to use again.`, "info");
      renderCartBar(); refreshAvailability(); loadInventory();
    };
    const cell = document.createElement("td"); cell.append(b);
    const r = row([i + 1, mono(l.msisdn), mono(l.iccid), simType(l.sim_type), l.plan_name, money(l.monthly_price), cell]);
    if (highlight.has(l.id)) r.className = "flash";
    return r;
  }));
}

// ---- SIM inventory list for this category
const inv = { filter: "", q: "", timer: null };
const when = iso => (iso ? iso.slice(0, 16).replace("T", " ") : "–");
async function loadInventory() {
  const params = new URLSearchParams({ limit: "200" });
  if (inv.filter) params.set("status", inv.filter);
  if (inv.q) params.set("q", inv.q);
  let d;
  try { d = await api("GET", `api/signups/${state.signup.id}/sims?${params}`); }
  catch (err) { $("#invShowing").textContent = `Could not load SIMs: ${err.message}`; return; }
  $("#invCat").textContent = d.category;
  const all = (d.counts.AVAILABLE || 0) + (d.counts.PAIRED || 0) + (d.counts.MINE || 0);
  document.querySelectorAll("#invTabs button").forEach(b => {
    b.classList.toggle("on", b.dataset.f === inv.filter);
    $("span", b).textContent = b.dataset.f ? (d.counts[b.dataset.f] ?? 0) : all;
  });
  $("#invShowing").textContent = d.total > d.pairs.length ? `Showing ${d.pairs.length} of ${d.total}` : `${d.total} SIM(s)`;
  $("#invTable tbody").replaceChildren(...(d.pairs.length ? d.pairs.map(x =>
    row([mono(x.iccid), simType(x.sim_type), statusPill(x.sim_status, x.in_my_cart), mono(x.msisdn || "–"),
         x.msisdn ? statusPill(x.number_status, x.in_my_cart) : pill("Waiting for number", "wait"), when(x.paired_at)]))
    : [row([Object.assign(td(inv.q || inv.filter ? "No SIMs match." : "No SIMs uploaded for this category yet."), { colSpan: 6 })])]));
}
document.querySelectorAll("#invTabs button").forEach(b => b.onclick = () => { inv.filter = b.dataset.f; loadInventory(); });
$("#invSearch").oninput = e => { clearTimeout(inv.timer); inv.timer = setTimeout(() => { inv.q = e.target.value.trim(); loadInventory(); }, 250); };
$("#toStep5").onclick = () => go(5);

// ---------------------------------------------------------------- step 5: review
function renderReview() {
  const s = state.signup, c = s.cart, ao = s.account_officer;
  dl($("#reviewCompany"), [["Name", s.company_name], ["UEN", s.uen], ["Address", s.company_address || "–"],
    ["Category", s.category], ["Contract", s.contract_filename]]);
  dl($("#reviewAo"), [["Name", ao.full_name], ["ID", `${ao.id_type} ${ao.id_number_masked}`], ["Email", ao.email],
    ["Mobile", ao.mobile], ["Merchant code", s.merchant_code]]);
  renderAiReview(s.contract_ai_review);
  $("#summaryTable tbody").replaceChildren(...c.summary.map(p => row([p.plan_name, money(p.unit_price), p.ESIM, p.PHYSICAL, money(p.subtotal)])));
  $("#total").textContent = `${c.line_count} lines · ${money(c.monthly_total)} / month`;
  $("#linesTable tbody").replaceChildren(...c.lines.map((l, i) => {
    const b = document.createElement("button");
    b.className = "link"; b.textContent = "Remove";
    b.onclick = async () => {
      state.signup.cart = await api("DELETE", `api/signups/${s.id}/cart/${l.id}`);
      state.signup.cart.line_count ? renderReview() : go(4);
    };
    const cell = document.createElement("td"); cell.append(b);
    return row([i + 1, l.msisdn, l.iccid, l.sim_type === "ESIM" ? "eSIM" : "Physical", l.plan_name, money(l.monthly_price), cell]);
  }));
  $("#confirm").checked = false;
  $("#submitOrder").disabled = true;
}
$("#confirm").onchange = e => $("#submitOrder").disabled = !e.target.checked;

function renderAiReview(r) {
  const box = $("#aiReview");
  if (!r || r.status === "disabled") return box.classList.add("hidden");
  box.classList.remove("hidden");
  const m = document.createElement("div");
  if (r.status === "pending") {
    m.className = "msg info"; m.textContent = "AI contract check in progress…";
    setTimeout(async () => {
      const s = await api("GET", `api/signups/${state.signup.id}`);
      state.signup.contract_ai_review = s.contract_ai_review;
      if (state.step === 5) renderAiReview(s.contract_ai_review);
    }, 3000);
  } else if (r.status === "unavailable") {
    m.className = "msg info"; m.textContent = "AI contract check unavailable. The contract will be reviewed manually.";
  } else {
    m.className = `msg ${r.status === "ok" ? "ok" : "err"}`;
    m.textContent = r.status === "ok"
      ? "AI contract check: the company name and UEN match the contract."
      : "AI contract check found items to review (advisory; you can still submit):\n• " + r.flags.join("\n• ");
  }
  box.replaceChildren(m);
}

$("#submitOrder").onclick = async () => {
  $("#submitOrder").disabled = true;
  try {
    const r = await api("POST", `api/signups/${state.signup.id}/submit`);
    sessionStorage.removeItem("signupId");
    state.submitted = true;
    go(6);
    pollStatus(r.account_number);
  } catch (err) { msg("msg5", err.message); $("#submitOrder").disabled = false; }
};

// ---------------------------------------------------------------- step 6: provisioning sequence
function timeline(a) {
  const lines = a.lines, n = lines.length;
  const prov = lines.filter(l => l.status === "PROVISIONED").length;
  const failed = lines.filter(l => l.status === "FAILED").length;
  const esim = lines.filter(l => l.sim_type === "ESIM");
  const qr = esim.filter(l => l.esim_qr_sent).length;
  const running = a.order_status === "SUBMITTED";
  const items = [
    ["ok", "Order submitted", `${n} line(s) for ${a.company.name}`],
    ["ok", "Company profile & account officer created", `${a.company.uen} · ${a.account_officer.name}`],
    [a.billing_account_ref ? "ok" : running ? "run" : "bad", "Billing account created", a.billing_account_ref || (running ? "In progress…" : "Failed")],
    [prov === n ? "ok" : running ? "run" : failed ? "bad" : "run", "Lines activated on network & billing",
      `${prov} of ${n} provisioned${failed ? ` · ${failed} failed (our team will retry)` : ""}`],
  ];
  if (esim.length) items.push([qr === esim.length ? "ok" : running ? "run" : "bad", "eSIM QR codes emailed",
    `${qr} of ${esim.length} sent to ${a.account_officer.email}`]);
  items.push([a.order_status === "COMPLETED" ? "ok" : running ? "" : "bad", "Ready to use",
    a.order_status === "COMPLETED" ? "All lines are live" : running ? "Waiting for the steps above" : "Some lines need attention"]);
  $("#timeline").replaceChildren(...items.map(([cls, t, d], i) => {
    const li = document.createElement("li");
    li.className = cls;
    li.innerHTML = `<span class="ic"></span><div class="tt"></div><div class="td"></div>`;
    $(".ic", li).textContent = cls === "ok" ? "✓" : cls === "bad" ? "!" : i + 1;
    $(".tt", li).textContent = t; $(".td", li).textContent = d;
    return li;
  }));
}

async function pollStatus(accountNumber) {
  const a = await api("GET", `api/accounts/${accountNumber}`);
  timeline(a);
  dl($("#statusInfo"), [["Account number", a.account_number], ["Company", `${a.company.name} (${a.company.uen})`],
    ["Account officer", a.account_officer.email], ["Billing account", a.billing_account_ref || "pending"],
    ["Order status", a.order_status.replace("_", " ").toLowerCase()]]);
  $("#statusLead").textContent = a.order_status === "COMPLETED" ? "All done. Your lines are active."
    : a.order_status === "SUBMITTED" ? "We're setting up your lines. This page updates automatically."
    : "Some lines could not be activated yet. Our team has been notified and will retry.";
  $("#statusTable tbody").replaceChildren(...a.lines.map(l => {
    const st = document.createElement("td"), pill = document.createElement("span");
    pill.className = `pill ${l.status}`; pill.textContent = l.status; if (l.error) pill.title = l.error;
    st.append(pill);
    return row([l.msisdn, l.iccid, l.sim_type === "ESIM" ? "eSIM" : "Physical", l.plan, st,
      l.sim_type === "ESIM" ? (l.esim_qr_sent ? "Emailed" : "Pending") : "–"]);
  }));
  if (a.order_status === "SUBMITTED") setTimeout(() => pollStatus(accountNumber), 2000);
}
$("#newSignup").onclick = () => location.reload();

// ---------------------------------------------------------------- resume a draft after reload
async function resume() {
  const id = sessionStorage.getItem("signupId");
  if (!id) return go(2);
  try {
    state.signup = await api("GET", `api/signups/${id}`);
    if (state.signup.status !== "DRAFT") throw new Error();
    lockCompanyForm(state.signup);
    if (!state.signup.contract_uploaded) return go(2);
    $("#subContract").classList.add("complete"); $("#toStep3").disabled = false;
    state.maxStep = 3;
    if (!state.signup.account_officer) return go(3);
    go(4);
  } catch { sessionStorage.removeItem("signupId"); go(2); }
}

api("GET", "api/auth/options").then(o => {
  if (!o.test_mode) return;
  $("#testEmail").textContent = o.test_email;
  $("#testMode").classList.remove("hidden");
}).catch(() => {});
signedIn().catch(() => go(1));
