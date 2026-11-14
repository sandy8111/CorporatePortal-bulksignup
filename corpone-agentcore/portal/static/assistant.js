"use strict";
// Cognito Managed Login (authorization code + PKCE) and chat relayed through /api/assistant/invoke.
const $ = s => document.querySelector(s);
const store = sessionStorage;            // tokens live only in this tab
const REDIRECT = location.origin + location.pathname;
let conf = null;

const b64url = bytes => btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const randomString = (n = 48) => b64url(crypto.getRandomValues(new Uint8Array(n)));
const claims = jwt => { try { return JSON.parse(atob(jwt.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))); } catch { return {}; } };
const show = id => ["#offCard", "#signInCard", "#chatCard"].forEach(c => { $(c).hidden = c !== id; });
function note(text, kind = "err") { const el = $("#signInMsg"); el.textContent = text; el.className = `msg ${kind}`; }

async function signIn() {
  const verifier = randomString(48), state = randomString(16);
  const challenge = b64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
  store.setItem("pkce_verifier", verifier); store.setItem("pkce_state", state);
  const q = new URLSearchParams({ response_type: "code", client_id: conf.client_id, redirect_uri: REDIRECT,
    scope: conf.scopes, state, code_challenge: challenge, code_challenge_method: "S256" });
  location.assign(`${conf.login_url}/oauth2/authorize?${q}`);
}

async function finishSignIn(code, state) {
  history.replaceState(null, "", REDIRECT);                 // drop ?code from the address bar
  if (!state || state !== store.getItem("pkce_state")) throw new Error("Sign-in response did not match; try again.");
  const res = await fetch(`${conf.login_url}/oauth2/token`, { method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "authorization_code", client_id: conf.client_id, code,
                                redirect_uri: REDIRECT, code_verifier: store.getItem("pkce_verifier") || "" }) });
  store.removeItem("pkce_verifier"); store.removeItem("pkce_state");
  if (!res.ok) throw new Error("Sign-in failed; try again.");
  const t = await res.json();
  store.setItem("access_token", t.access_token); store.setItem("id_token", t.id_token || "");
  store.setItem("expires_at", String(Date.now() + (t.expires_in || 3600) * 1000));
}

function signedIn() { return store.getItem("access_token") && Number(store.getItem("expires_at")) > Date.now() + 30000; }
function signOut() {
  ["access_token", "id_token", "expires_at", "session_id"].forEach(k => store.removeItem(k));
  location.assign(`${conf.login_url}/logout?${new URLSearchParams({ client_id: conf.client_id, logout_uri: REDIRECT })}`);
}

function sessionId(fresh = false) {
  if (fresh || !store.getItem("session_id")) store.setItem("session_id", `assistant-${crypto.randomUUID()}`);  // 46 chars
  return store.getItem("session_id");
}

function bubble(text, cls, tools) {
  const b = document.createElement("div"); b.className = `bubble ${cls}`; b.textContent = text;
  if (tools && tools.length) { const t = document.createElement("span"); t.className = "tools"; t.textContent = `tools: ${tools.join(", ")}`; b.append(t); }
  $("#chat").append(b); $("#chat").scrollTop = $("#chat").scrollHeight; return b;
}

async function send(prompt) {
  bubble(prompt, "user");
  const wait = bubble("…", "bot"); $("#send").disabled = true;
  try {
    const res = await fetch("api/assistant/invoke", { method: "POST", cache: "no-store",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${store.getItem("access_token")}` },
      body: JSON.stringify({ prompt, session_id: sessionId() }) });
    const data = await res.json().catch(() => ({}));
    wait.remove();
    if (res.status === 401) { bubble("Your sign-in has expired. Signing you in again…", "err"); setTimeout(signIn, 1200); return; }
    if (!res.ok) return bubble(data.detail || `Something went wrong (HTTP ${res.status}).`, "err");
    bubble(data.response || "(no answer)", data.status === "success" ? "bot" : "err", data.tools_used);
  } catch { wait.remove(); bubble("Could not reach the assistant.", "err"); }
  finally { $("#send").disabled = false; }
}

function startChat() {
  const access = claims(store.getItem("access_token")), id = claims(store.getItem("id_token"));
  const groups = [].concat(access["cognito:groups"] || []);
  const role = groups.includes("ops") ? "ops" : "officer";
  $("#whoEmail").textContent = id.email || access.username || "signed in";
  $("#whoRole").textContent = role === "ops" ? "CorpOne ops" : "account officer";
  const ideas = role === "ops"
    ? ["Which SIM categories are low on eSIM lines?", "Status of account CA0000000001?", "Is UEN 197401143C blacklisted?"]
    : ["What plans do you offer?", "Is UEN 197401143C valid?", "What do I need to prepare for a sign-up?"];
  $("#suggest").replaceChildren(...ideas.map(q => Object.assign(document.createElement("button"),
    { type: "button", className: "secondary", textContent: q, onclick: () => send(q) })));
  show("#chatCard");
  if (!$("#chat").children.length) bubble(`Hi! I'm the CorpOne assistant. ${role === "ops"
    ? "I can check inventory, blacklist and order status, and retry provisioning after you confirm."
    : "I can compare plans, check a UEN and explain the sign-up steps."}`, "bot");
}

$("#signIn").onclick = () => signIn();
$("#signOut").onclick = () => signOut();
$("#newChat").onclick = () => { sessionId(true); $("#chat").replaceChildren(); startChat(); };
$("#composer").onsubmit = e => { e.preventDefault(); const p = $("#prompt").value.trim(); if (p) { $("#prompt").value = ""; send(p); } };
$("#prompt").onkeydown = e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#composer").requestSubmit(); } };

(async () => {
  conf = await (await fetch("api/assistant/config", { cache: "no-store" })).json().catch(() => ({ enabled: false }));
  if (!conf.enabled) return show("#offCard");
  const q = new URLSearchParams(location.search);
  try {
    if (q.get("error")) throw new Error(q.get("error_description") || "Sign-in was cancelled.");
    if (q.get("code")) await finishSignIn(q.get("code"), q.get("state"));
  } catch (err) { show("#signInCard"); return note(err.message); }
  signedIn() ? startChat() : show("#signInCard");
})();
