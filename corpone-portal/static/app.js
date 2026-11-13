"use strict";
const $ = (s, el = document) => el.querySelector(s);
const state = { signup: null, plan: null, email: null, otpEmail: null };

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  const res = await fetch(path, opts);
  const data = res.status === 204 ? null : await res.json().catch(() => ({}));
  if (res.status === 401 && !path.startsWith("/api/auth/")) { showLogin(); throw new Error("Please sign in again"); }
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
  el.textContent = text; el.className = `msg ${kind}`;
  if (!text) el.classList.add("hidden");
}

function td(text) { const c = document.createElement("td"); c.textContent = text ?? ""; return c; }
function row(cells) { const r = document.createElement("tr"); cells.forEach(c => r.append(c instanceof Node ? c : td(c))); return r; }

function go(step) {
  document.querySelectorAll("section.step").forEach(s => s.classList.toggle("hidden", s.dataset.step != step));
  document.querySelectorAll("#steps li").forEach(li => {
    li.classList.toggle("active", li.dataset.step == step);
    li.classList.toggle("done", li.dataset.step < step);
  });
  if (step === 3) loadStep3();
  if (step === 4) renderReview();
  window.scrollTo(0, 0);
}
document.querySelectorAll("[data-back]").forEach(b => b.onclick = () => go(+b.dataset.back));

// ---- Login (email OTP)
function showLogin() {
  $("#wizard").classList.add("hidden"); $("#whoami").classList.add("hidden");
  $("#login").classList.remove("hidden");
}

async function requestCode(email) {
  await api("POST", "/api/auth/otp/request", { email });
  state.otpEmail = email;
  $("#otpEmail").textContent = email;
  $("#otpRequestForm").classList.add("hidden");
  $("#otpVerifyForm").classList.remove("hidden");
  $("#otpVerifyForm").elements.code.focus();
  msg("msgLogin", "Code sent. It expires in 5 minutes.", "ok");
}

$("#otpRequestForm").onsubmit = async e => {
  e.preventDefault();
  try { await requestCode(e.target.elements.email.value.trim()); } catch (err) { msg("msgLogin", err.message); }
};
$("#otpResend").onclick = async () => {
  try { await requestCode(state.otpEmail); } catch (err) { msg("msgLogin", err.message); }
};
$("#otpVerifyForm").onsubmit = async e => {
  e.preventDefault();
  try {
    await api("POST", "/api/auth/otp/verify", { email: state.otpEmail, code: e.target.elements.code.value });
    msg("msgLogin", "");
    e.target.reset();
    await enterApp();
  } catch (err) { msg("msgLogin", err.message); }
};
$("#logout").onclick = async e => {
  e.preventDefault();
  await api("POST", "/api/auth/logout").catch(() => {});
  sessionStorage.removeItem("signupId");
  location.href = "/";
};

async function enterApp() {
  const me = await api("GET", "/api/auth/me");
  state.email = me.email;
  $("#whoEmail").textContent = me.email;
  $("#whoami").classList.remove("hidden");
  $("#boLink").classList.add("hidden");
  $("#login").classList.add("hidden");
  $("#wizard").classList.remove("hidden");
  $("#aoForm").elements.email.value = me.email;
  await resume();
}

// ---- Step 1: company + contract
$("#companyForm").onsubmit = async e => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target));
  try {
    state.signup = await api("POST", "/api/signups", f);
    sessionStorage.setItem("signupId", state.signup.id);
    msg("msg1", `UEN ${state.signup.uen} is valid. Please upload the signed contract agreement.`, "ok");
    e.target.querySelectorAll("input,textarea,button").forEach(i => i.disabled = true);
    $("#contractBox").classList.remove("hidden");
  } catch (err) { msg("msg1", err.message); }
};

$("#contractForm").onsubmit = async e => {
  e.preventDefault();
  try {
    const r = await api("POST", `/api/signups/${state.signup.id}/contract`, new FormData(e.target));
    msg("msg1", `Contract "${r.contract_filename}" uploaded securely.`, "ok");
    $("#toStep2").disabled = false;
  } catch (err) { msg("msg1", err.message); }
};
$("#toStep2").onclick = () => go(2);

// ---- Step 2: account officer
$("#aoForm").onsubmit = async e => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target));
  delete f.email;  // server uses the verified login email
  try {
    state.signup = await api("PUT", `/api/signups/${state.signup.id}/account-officer`, f);
    msg("msg2", "");
    go(3);
  } catch (err) { msg("msg2", err.message); }
};

// ---- Step 3: plans, SIM upload, cart
async function loadStep3() {
  if (!$("#plans").children.length) {
    const plans = await api("GET", "/api/plans");
    for (const p of plans) {
      const card = document.createElement("div");
      card.className = "plan";
      card.innerHTML = `<div class="name"></div><div class="price"></div><div class="desc"></div>`;
      $(".name", card).textContent = p.name;
      $(".price", card).textContent = `$${Number(p.monthly_price).toFixed(0)}/mth`;
      $(".desc", card).textContent = p.description;
      card.onclick = () => {
        state.plan = p.code;
        document.querySelectorAll(".plan").forEach(c => c.classList.toggle("selected", c === card));
      };
      $("#plans").append(card);
    }
  }
  refreshAvailability();
  renderMiniCart();
}

async function refreshAvailability() {
  const a = await api("GET", `/api/signups/${state.signup.id}/availability`);
  msg("avail", `Category ${a.category}: ${a.ESIM} eSIM and ${a.PHYSICAL} physical SIM lines are paired and ready to allocate.`, "info");
}

$("#simForm").onsubmit = async e => {
  e.preventDefault();
  try {
    const r = await api("POST", `/api/signups/${state.signup.id}/sims`, new FormData(e.target));
    let t = `${r.accepted} SIM(s) accepted, ${r.paired} newly paired with numbers.`;
    if (r.rejected_count) t += `\n${r.rejected_count} rejected:\n` + r.rejected.slice(0, 20).map(x => `  row ${x.row} (${x.value}): ${x.error}`).join("\n");
    msg("msgSim", t, r.rejected_count ? "err" : "ok");
    e.target.reset();
    refreshAvailability();
  } catch (err) { msg("msgSim", err.message); }
};

$("#cartForm").onsubmit = async e => {
  e.preventDefault();
  if (!state.plan) return msg("msg3", "Please select a plan first.");
  const f = Object.fromEntries(new FormData(e.target));
  try {
    state.signup.cart = await api("POST", `/api/signups/${state.signup.id}/cart`,
      { plan_code: state.plan, sim_type: f.sim_type, quantity: +f.quantity });
    msg("msg3", "Lines paired and added to cart.", "ok");
    refreshAvailability();
    renderMiniCart();
  } catch (err) { msg("msg3", err.message); }
};

function renderMiniCart() {
  const c = state.signup.cart;
  $("#miniCart").textContent = c.line_count ? `Cart: ${c.line_count} line(s), $${c.monthly_total}/month` : "Cart is empty.";
  $("#toStep4").disabled = !c.line_count;
}
$("#toStep4").onclick = () => go(4);

// ---- Step 4: review
function renderReview() {
  const s = state.signup, c = s.cart, ao = s.account_officer;
  const info = [["Company", `${s.company_name} (UEN ${s.uen})`], ["Address", s.company_address || "-"],
    ["Contract", s.contract_filename], ["Account officer", `${ao.full_name} (${ao.id_type} ${ao.id_number_masked})`],
    ["AO email / mobile", `${ao.email} / ${ao.mobile}`], ["Merchant code", s.merchant_code], ["Category", s.category]];
  $("#reviewInfo").replaceChildren(...info.flatMap(([k, v]) => {
    const dt = document.createElement("dt"), dd = document.createElement("dd");
    dt.textContent = k; dd.textContent = v; dd.style.margin = 0; return [dt, dd];
  }));
  renderAiReview(s.contract_ai_review);
  $("#summaryTable tbody").replaceChildren(...c.summary.map(p => row([p.plan_name, `$${p.unit_price}`, p.ESIM, p.PHYSICAL, `$${p.subtotal}`])));
  $("#total").textContent = `${c.line_count} lines · Total $${c.monthly_total} / month`;
  $("#linesTable tbody").replaceChildren(...c.lines.map((l, i) => {
    const b = document.createElement("button");
    b.className = "link"; b.textContent = "Remove";
    b.onclick = async () => {
      state.signup.cart = await api("DELETE", `/api/signups/${s.id}/cart/${l.id}`);
      state.signup.cart.line_count ? renderReview() : go(3);
    };
    const cell = document.createElement("td"); cell.append(b);
    return row([i + 1, l.msisdn, l.iccid, l.sim_type === "ESIM" ? "eSIM" : "Physical", l.plan_name, `$${l.monthly_price}`, cell]);
  }));
  $("#confirm").checked = false;
  $("#submitOrder").disabled = true;
}
function renderAiReview(r) {
  const box = $("#aiReview");
  if (!r || r.status === "disabled") return box.classList.add("hidden");
  box.classList.remove("hidden");
  box.replaceChildren();
  const head = document.createElement("div");
  if (r.status === "pending") {
    head.className = "msg info"; head.textContent = "AI contract check in progress…";
    box.append(head);
    setTimeout(async () => {
      const s = await api("GET", `/api/signups/${state.signup.id}`);
      state.signup.contract_ai_review = s.contract_ai_review;
      if ($("section.step[data-step='4']:not(.hidden)")) renderAiReview(s.contract_ai_review);
    }, 3000);
    return;
  }
  if (r.status === "unavailable") {
    head.className = "msg info"; head.textContent = "AI contract check unavailable; the contract will be reviewed manually.";
    return box.append(head);
  }
  head.className = `msg ${r.status === "ok" ? "ok" : "err"}`;
  head.textContent = r.status === "ok"
    ? "AI contract check: company name and UEN match the contract."
    : "AI contract check found items to review (advisory; you can still submit):\n• " + r.flags.join("\n• ");
  box.append(head);
}

$("#confirm").onchange = e => $("#submitOrder").disabled = !e.target.checked;

$("#submitOrder").onclick = async () => {
  $("#submitOrder").disabled = true;
  try {
    const r = await api("POST", `/api/signups/${state.signup.id}/submit`);
    sessionStorage.removeItem("signupId");
    go(5);
    pollStatus(r.account_number);
  } catch (err) { msg("msg4", err.message); $("#submitOrder").disabled = false; }
};

// ---- Step 5: provisioning status
async function pollStatus(accountNumber) {
  const a = await api("GET", `/api/accounts/${accountNumber}`);
  const info = [["Account number", a.account_number], ["Company", `${a.company.name} (${a.company.uen})`],
    ["Account officer", `${a.account_officer.name} <${a.account_officer.email}>`],
    ["Billing account", a.billing_account_ref || "pending"], ["Order status", a.order_status]];
  $("#statusInfo").replaceChildren(...info.flatMap(([k, v]) => {
    const dt = document.createElement("dt"), dd = document.createElement("dd");
    dt.textContent = k; dd.textContent = v; dd.style.margin = 0; return [dt, dd];
  }));
  $("#statusTable tbody").replaceChildren(...a.lines.map(l => {
    const st = document.createElement("td"), pill = document.createElement("span");
    pill.className = `pill ${l.status}`; pill.textContent = l.status; if (l.error) pill.title = l.error;
    st.append(pill);
    return row([l.msisdn, l.iccid, l.sim_type === "ESIM" ? "eSIM" : "Physical", l.plan, st,
      l.sim_type === "ESIM" ? (l.esim_qr_sent ? "Emailed to AO" : "Pending") : "-"]);
  }));
  if (a.order_status === "SUBMITTED") setTimeout(() => pollStatus(accountNumber), 2000);
}

// ---- Resume an unfinished draft after page reload
async function resume() {
  const id = sessionStorage.getItem("signupId");
  if (!id) return go(1);
  try {
    state.signup = await api("GET", `/api/signups/${id}`);
    if (state.signup.status !== "DRAFT") throw new Error();
    if (!state.signup.contract_uploaded) {
      go(1);
      const f = $("#companyForm");
      for (const k of ["uen", "company_name", "category", "company_address"]) f.elements[k].value = state.signup[k] || "";
      f.querySelectorAll("input,textarea,button").forEach(i => i.disabled = true);
      $("#contractBox").classList.remove("hidden");
    } else go(state.signup.account_officer ? 3 : 2);
  } catch { sessionStorage.removeItem("signupId"); go(1); }
}

enterApp().catch(showLogin);
