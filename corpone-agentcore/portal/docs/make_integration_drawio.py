"""Generate docs/integration.drawio: integration points and credentials (same content as integration.png).

Usage: python3 docs/make_integration_drawio.py docs/integration.drawio
Once the .drawio file is edited by hand in draw.io, treat it as the source and stop regenerating it.
"""
import sys
from xml.sax.saxutils import quoteattr

OUT = sys.argv[1]
cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
_n = [0]

BASE = "rounded=1;whiteSpace=wrap;html=1;arcSize=6;align=left;verticalAlign=top;spacingLeft=8;spacingTop=5;spacingRight=6;fontSize=11;"
USER = BASE + "fillColor=#e8f0fe;strokeColor=#0a58ca;strokeWidth=1.5;verticalAlign=middle;"
USER_D = USER + "dashed=1;"
AWS = BASE + "fillColor=#fff4e5;strokeColor=#c77700;strokeWidth=1.5;verticalAlign=middle;"
DISK = BASE + "fillColor=#e7f6ec;strokeColor=#1e7e34;strokeWidth=1.5;verticalAlign=middle;"
EXT = BASE + "fillColor=#f1f3f6;strokeColor=#6b7787;strokeWidth=1.5;dashed=1;verticalAlign=middle;"
CRED = BASE + "fillColor=#fffbea;strokeColor=#b7791f;strokeWidth=1.5;"
CRED_NONE = BASE + "fillColor=#f7f8fa;strokeColor=#c4ccd8;strokeWidth=1.5;"
APP = "rounded=1;whiteSpace=wrap;html=1;arcSize=2;fillColor=#fcfdff;strokeColor=#1b2533;strokeWidth=2;verticalAlign=top;align=left;spacingLeft=12;spacingTop=8;fontSize=14;fontStyle=1;"
EP = BASE + "fillColor=#ffffff;strokeColor=#9aa5b4;"
SECT = "text;html=1;align=left;verticalAlign=middle;fontSize=10;fontStyle=1;fontColor=#6b7787;"
NOTE = BASE + "fillColor=#fffaf2;strokeColor=#c77700;dashed=1;strokeWidth=1.5;"
TEXT = "text;html=1;align=left;verticalAlign=top;whiteSpace=wrap;"
HDR = TEXT + "fontSize=11;fontStyle=1;fontColor=#6b7787;"
LINK = "edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;endFill=1;strokeColor=#9aa5b4;strokeWidth=1.5;"


def box(label, style, x, y, w, h):
    _n[0] += 1
    cid = f"c{_n[0]}"
    cells.append(f'<mxCell id="{cid}" value={quoteattr(label)} style={quoteattr(style)} vertex="1" parent="1">'
                 f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
    return cid


def link(a, b, exit=(1, 0.5), entry=(0, 0.5)):
    _n[0] += 1
    st = LINK + f"exitX={exit[0]};exitY={exit[1]};exitDx=0;exitDy=0;entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;"
    cells.append(f'<mxCell id="l{_n[0]}" style={quoteattr(st)} edge="1" parent="1" source="{a}" target="{b}">'
                 f'<mxGeometry relative="1" as="geometry"/></mxCell>')


def mono(t):
    return f'<span style="font-family:Consolas,Menlo,monospace;font-size:10px;">{t}</span>'


TAGS = {"dev": ("#8a4b00", "DEV"), "prod": ("#073f91", "PROD"), "prop": ("#5b21b6", "PROPOSED"), "gap": ("#9b1c1c", "GAP")}


def tag(kind, text):
    color, word = TAGS[kind]
    return f'<br><span style="font-size:10px;font-weight:bold;color:{color};">{word}: {text}</span>'


def card(title, body):
    return f'<b>{title}</b><br><span style="color:#5b6675;">{body}</span>'


def cred(num, proto, key, body, extra="", none=False):
    head = (f'<span style="background:#1b2533;color:#fff;font-size:10px;font-weight:bold;">&nbsp;{num}&nbsp;</span>&nbsp;'
            f'<span style="color:#3c4858;">{proto}</span><br>')
    k = f'<b style="color:{"#5b6675" if none else "#7c4a03"};">{"NONE" if none else "KEY"} · {key}</b><br>'
    return head + k + body + extra


# ---------------------------------------------------------------- layout
CX, CW = 30, 210      # callers
IX, IW = 270, 330     # inbound credential
AX, AW = 630, 380     # app
OX, OW = 1040, 340    # outbound credential
SX, SW = 1410, 230    # systems
Y0, RH, GAP = 110, 136, 14


def ry(r):
    return Y0 + r * (RH + GAP)


box('<span style="font-size:22px;font-weight:bold;">CorpOne Corporate Portal · integration points and credentials</span><br>'
    '<span style="font-size:12px;color:#6b7787;">Every connection into and out of the app, the credential it uses, and how that '
    'credential is stored · test environment today, production recommendation tagged</span>', TEXT, 30, 15, 1400, 55)
for x, w, t in ((CX, CW, "CALLERS"), (IX, IW, "INBOUND · PROTOCOL AND CREDENTIAL"), (AX, AW, "CORPONE-PORTAL (FASTAPI :8501)"),
                (OX, OW, "OUTBOUND · PROTOCOL AND CREDENTIAL"), (SX, SW, "SYSTEMS CALLED")):
    box(t, HDR, x, 82, w, 20)

# ---------------------------------------------------------------- app box and its entry points
app_h = ry(6) + RH - Y0
app = box('corpone-portal<br><span style="font-weight:normal;font-size:11px;color:#6b7787;">uvicorn :8501 · secrets read from '
          '.env / CORPONE_* at startup</span>', APP, AX, Y0, AW, app_h)
y = Y0 + 52
box("ENTRY POINTS (AUTH CHECK)", SECT, AX + 12, y, AW - 24, 18); y += 22
eps = [
    ("/api/auth", "OTP request / verify<br><b style='color:#1e7e34'>check:</b> HMAC-SHA256(pepper, email:code), 5 min, 5 attempts, rate limits"),
    ("/api/signups, /api/companies, /api/merchants", "<b style='color:#1e7e34'>check:</b> current_ao: SHA-256(token) in ao_sessions, not expired or revoked; owner match"),
    ("/api/admin/*", "<b style='color:#1e7e34'>check:</b> require_admin: constant-time compare with CORPONE_ADMIN_TOKEN"),
    ("/gateway/v1/*", "<b style='color:#1e7e34'>check:</b> key prefix lookup, constant-time compare SHA-256(secret), active, scope, per-key rate limit, audit row"),
]
ep_ids = []
for t, b in eps:
    ep_ids.append(box(f"<b>{t}</b><br>{b}", EP, AX + 12, y, AW - 24, 70)); y += 80
box("OUTBOUND", SECT, AX + 12, y, AW - 24, 18); y += 22
out_gw = box("<b>Outbound gateway</b> (network, billing, ai)<br>retries + backoff, circuit breaker, rate limit, outbound_call_logs",
             EP, AX + 12, y, AW - 24, 56); y += 66
clients = box("<b>boto3 / smtplib / SDK clients</b><br>S3, SES, SMTP, Anthropic, Bedrock", EP, AX + 12, y, AW - 24, 50); y += 60
box("NEVER LOGGED", SECT, AX + 12, y, AW - 24, 18); y += 22
box("request/response bodies, OTP codes, tokens, API keys (log line has method, path, status, ms, caller, request ID only)",
    EP, AX + 12, y, AW - 24, 56)

# ---------------------------------------------------------------- inbound rows
inbound = [
    (card("Account officer", "browser · sign-up portal"), USER,
     cred("I1", "HTTPS · /api/auth then /api/signups", "Email OTP, then session",
          f"6-digit code to the email, then cookie {mono('corpone_session')} (HttpOnly, SameSite=Strict, 8 h, Secure in prod) "
          f"or {mono('Authorization: Bearer &lt;session token&gt;')}", tag("dev", "fixed code 123456 for one test email")), 0),
    (card("Back office user", "browser · admin page"), USER,
     cred("I2", "HTTPS · /api/admin/*", "Admin token",
          f"header {mono('X-Admin-Token')} = {mono('CORPONE_ADMIN_TOKEN')}; kept in the tab's sessionStorage",
          tag("gap", "one shared token, no per-user identity")), 2),
    (card("Partner system", "dealer CRM, ops tools"), USER,
     cred("I3", "HTTPS · /gateway/v1/*", "Partner API key",
          f"{mono('X-API-Key: cpk_&lt;prefix&gt;_&lt;secret&gt;')} or {mono('Authorization: Bearer cpk_…')}; issued in the back "
          "office, shown once; scopes and rate limit per key"), 3),
    (card("mcp_server", "MCP tools for AI agents (stdio or :8000/mcp)"), USER,
     cred("I4", "HTTPS · /gateway/v1/* (same path as partners)", "Gateway API key",
          f"{mono('CORPONE_GATEWAY_API_KEY')} sent as {mono('Authorization: Bearer cpk_…')} to {mono('CORPONE_GATEWAY_URL')}",
          tag("prod", "read-only scopes, key in AgentCore Identity or Secrets Manager")), 3),
]
caller_ids = {}
for r, (who, wst, cr, target) in enumerate(inbound):
    c = box(who, wst, CX, ry(r) + 38, CW, 64)
    k = box(cr, CRED, IX, ry(r), IW, RH)
    caller_ids[r] = c
    link(c, k)
    link(k, ep_ids[target], entry=(0, 0.5))

# I5: agent to mcp_server (not to the app)
agent = box(card("AI agent", "Claude Desktop / Code, Strands agent, AgentCore"), USER, CX, ry(4) + 38, CW, 64)
k5 = box(cred("I5", "MCP · to mcp_server (not to the app)", "Agent to MCP server", "stdio: local process, no network credential",
              tag("prop", "AgentCore Runtime / Gateway with Cognito JWT or IAM SigV4")), CRED, IX, ry(4), IW, RH)
link(agent, k5)
link(k5, caller_ids[3], exit=(0.5, 0), entry=(0.5, 1))

# Edge note and D1
box('<b>Edge (test):</b> browser, then CloudFront (HTTPS), then nginx :80, then ' + mono('/ports/8501/') + ' to the app. '
    'This path has <b>no login of its own</b>; every API call is still checked by the app (I1–I3). API responses are '
    + mono('no-store, private') + ' so CloudFront never caches them.' + tag("gap", "behind nginx the client IP is the proxy's")
    + tag("prod", "load balancer over HTTPS; trust X-Forwarded-For from it only"), NOTE, CX, ry(5) + 8, IX + IW - CX, RH - 16)
d_in = box(card("eSIM data in", "SIM CSV: smdp_address, matching_id"), USER_D, CX, ry(6) + 38, CW, 64)
d1 = box(cred("D1", "upload via I2 / I3 / portal SIM upload", "Activation code, not a key",
              f"{mono('LPA:1$&lt;SM-DP+&gt;$&lt;MatchingID&gt;')} built per eSIM; QR emailed only to the AO's verified login email (O3)",
              none=True), CRED_NONE, IX, ry(6), IW, RH)
link(d_in, d1)
link(d1, app, entry=(0, 0.93))

# ---------------------------------------------------------------- outbound rows
outbound = [
    (cred("O1", "SQLAlchemy · CORPONE_DATABASE_URL", "Database URL", "SQLite file: no credential",
          tag("prod", "Postgres user/password from Secrets Manager or IAM auth, TLS")),
     card("Database", "SQLite data/corpone.db") + tag("prod", "RDS Postgres"), DISK, clients),
    (cred("O2", "HTTPS · S3 PutObject · SSE AES256", "AWS credentials",
          f"boto3 default chain (env, profile, instance or task role); bucket {mono('CORPONE_S3_BUCKET')}, region {mono('CORPONE_AWS_REGION')}",
          tag("prod", "IAM role with s3:PutObject on the bucket only")),
     card("Contracts store", "Amazon S3 (encrypted)") + tag("dev", "local data/uploads/"), AWS, clients),
    (cred("O3", "Email · sign-in codes, eSIM QR codes", "SES or SMTP",
          f"SES: AWS credential chain (ses:SendRawEmail). SMTP: {mono('CORPONE_SMTP_HOST/PORT/USER/PASSWORD')}, STARTTLS. "
          f"From {mono('CORPONE_EMAIL_FROM')}", tag("dev", ".eml files, no credential")),
     card("Email delivery", "Amazon SES or SMTP relay") + tag("dev", "data/outbox/*.eml"), AWS, clients),
    (cred("O4", "HTTPS POST · line activation", "Telco API key",
          f"{mono('Authorization: Bearer CORPONE_TELCO_API_KEY')} to {mono('CORPONE_NETWORK_API_BASE_URL')}; "
          f"{mono('Idempotency-Key: line-&lt;id&gt;-network')}", tag("dev", "mock, no call")),
     card("Network API", "activates SIM + number"), EXT, out_gw),
    (cred("O5", "HTTPS POST · billing account, subscriptions", "Telco API key",
          f"same {mono('CORPONE_TELCO_API_KEY')} to {mono('CORPONE_BILLING_API_BASE_URL')}; "
          f"{mono('Idempotency-Key: account-&lt;id&gt;')}, {mono('line-&lt;id&gt;-billing')}", tag("dev", "mock")),
     card("Billing API", "accounts, subscriptions"), EXT, out_gw),
    (cred("O6", "HTTPS · Messages API · contract review", "Anthropic API key",
          f"{mono('ANTHROPIC_API_KEY')} (read by the SDK) when {mono('CORPONE_AI_PROVIDER=anthropic')}", tag("dev", "AI disabled")),
     card("Claude · Anthropic API", "advisory contract check"), EXT, out_gw),
    (cred("O7", "HTTPS · Bedrock (Mantle client) · contract review", "AWS credentials",
          f"AWS credential chain (bedrock:InvokeModel) when {mono('CORPONE_AI_PROVIDER=bedrock')}; fallback model "
          f"{mono('CORPONE_AI_BEDROCK_FALLBACK_MODEL')}"),
     card("Claude · Amazon Bedrock", "same review, AWS-hosted"), AWS, out_gw),
]
for r, (cr, sys_label, sst, src) in enumerate(outbound):
    k = box(cr, CRED, OX, ry(r), OW, RH)
    s = box(sys_label, sst, SX, ry(r) + 34, SW, 72)
    link(app, k, exit=(1, (ry(r) + RH / 2 - Y0) / app_h))
    link(k, s)

# ---------------------------------------------------------------- key and secret register (editable HTML table)
rows = [
    ("I1", "Sign-in code", "email · CORPONE_OTP_PEPPER", "AO sign-in", "Only HMAC-SHA256(pepper, email:code) stored; 5 min, single use, 5 attempts",
     "random per request; fixed 123456 for admin@corp1.com.sg", "pepper from Secrets Manager; fixed code never set"),
    ("I1", "Session token", "cookie corpone_session / Bearer", "AO API calls", "32 random bytes; only SHA-256 stored in ao_sessions; 8 h; revoked on logout",
     "cookie without Secure flag", "CORPONE_COOKIE_SECURE=true over HTTPS"),
    ("I2", "Admin token", "X-Admin-Token · CORPONE_ADMIN_TOKEN", "whole back office", "constant-time compare with the env value; browser keeps it in sessionStorage",
     "random token set at server start", "Secrets Manager + rotation; better: per-user SSO (IAM Identity Center / OIDC)"),
    ("I3, I4", "Gateway API key", "X-API-Key / Bearer cpk_… · CORPONE_GATEWAY_API_KEY", "partners, MCP server",
     "prefix + SHA-256 of secret stored; shown once; scopes, per-key rate limit, revocable, audited", "issued in back office",
     "one key per client, least scopes; caller keeps it in Secrets Manager / AgentCore Identity"),
    ("I5", "Agent identity", "JWT or SigV4", "agent to MCP server", "checked by AgentCore (not by the app)", "stdio, local only",
     "Cognito client-credentials JWT or IAM"),
    ("O1", "Database credential", "CORPONE_DATABASE_URL", "all data", "connection string", "SQLite file",
     "Postgres secret in Secrets Manager or IAM DB auth; TLS"),
    ("O2, O3, O7", "AWS credentials", "boto3 / SDK default chain", "S3, SES, Bedrock", "never stored by the app",
     "not used (local storage, console email, AI off)", "IAM role (ECS task / EC2 instance), least privilege; no access keys in env"),
    ("O3", "SMTP password", "CORPONE_SMTP_USER / _PASSWORD", "email via SMTP", "env value; STARTTLS", "not used",
     "Secrets Manager, or SES with IAM instead"),
    ("O4, O5", "Telco API key", "Authorization: Bearer · CORPONE_TELCO_API_KEY", "Network and Billing APIs",
     "env value; plus Idempotency-Key per call", "mock mode, not used", "Secrets Manager; separate keys per upstream if supported; mTLS if offered"),
    ("O6", "Anthropic API key", "ANTHROPIC_API_KEY", "contract review", "read by the Anthropic SDK", "AI disabled",
     "Secrets Manager, or use Bedrock (O7) with IAM"),
    ("D1", "eSIM activation code", "SIM CSV smdp_address, matching_id", "eSIM QR codes", "stored with the SIM; emailed only to the verified AO email",
     "default SM-DP+ smdp.corpone.sg", "treat as sensitive: restrict upload, encrypt DB at rest"),
]
th = "border:1px solid #dfe4ec;padding:4px 6px;background:#f1f3f6;text-align:left;font-size:10px;color:#3c4858;"
td = "border:1px solid #dfe4ec;padding:4px 6px;vertical-align:top;font-size:11px;"
heads = ["#", "Credential", "Setting / header", "Used for", "How it's stored or checked", "Today (test)", "Production"]
table = ('<span style="font-size:16px;font-weight:bold;">Key and secret register</span><br>'
         '<span style="font-size:11px;color:#5b6675;">Where each credential lives today, how the app stores or checks it, and what '
         'production should use.</span><br><br><table style="border-collapse:collapse;width:100%;">'
         "<tr>" + "".join(f'<th style="{th}">{h}</th>' for h in heads) + "</tr>"
         + "".join("<tr>" + "".join(f'<td style="{td}">{c}</td>' for c in r) + "</tr>" for r in rows) + "</table>")
TY = ry(6) + RH + 40
box(table, TEXT + "overflow=fill;", 30, TY, SX + SW - 30, 430)

# legend
LY = TY + 440
box('<span style="font-size:11px;color:#3c4858;"><b style="color:#7c4a03;">KEY</b> credential · '
    '<b style="color:#8a4b00;">DEV</b> test setup today · <b style="color:#073f91;">PROD</b> production recommendation · '
    '<b style="color:#5b21b6;">PROPOSED</b> not built yet · <b style="color:#9b1c1c;">GAP</b> known weakness · '
    'blue = callers · green = data · orange = AWS service · dashed grey = external system</span>', TEXT, 30, LY, 1200, 24)

xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<mxfile host="Electron" agent="corpone-docs-generator" version="24.7.0">'
       '<diagram id="integration" name="Integration and credentials"><mxGraphModel dx="1600" dy="900" grid="1" gridSize="10" '
       f'guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1680" pageHeight="{LY + 50}" '
       'math="0" shadow="0"><root>' + "".join(cells) + "</root></mxGraphModel></diagram></mxfile>\n")
open(OUT, "w", encoding="utf-8").write(xml)
print(f"wrote {OUT}: {len(cells) - 2} cells")
