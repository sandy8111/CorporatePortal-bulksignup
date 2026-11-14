"""Generate docs/architecture.drawio: the layered architecture diagram (same content as architecture.png).

Usage: python3 docs/make_architecture_drawio.py docs/architecture.drawio
Once the .drawio file is edited by hand in draw.io, treat it as the source and stop regenerating it.
"""
import sys
from xml.sax.saxutils import quoteattr

OUT = sys.argv[1]
cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
_n = [0]

BASE = "rounded=1;whiteSpace=wrap;html=1;arcSize=8;align=left;verticalAlign=top;spacingLeft=8;spacingTop=4;spacingRight=6;fontSize=12;"
USER = BASE + "fillColor=#e8f0fe;strokeColor=#0a58ca;strokeWidth=1.5;"
EDGE = BASE + "fillColor=#fff4e5;strokeColor=#c77700;strokeWidth=1.5;"
SVC = BASE + "fillColor=#fafbfc;strokeColor=#9aa5b4;fontSize=11;"
MEM = BASE + "fillColor=#fdecea;strokeColor=#c62828;fontSize=11;"
DISK = BASE + "fillColor=#e7f6ec;strokeColor=#1e7e34;strokeWidth=1.5;"
EXT = BASE + "fillColor=#f1f3f6;strokeColor=#6b7787;dashed=1;strokeWidth=1.5;"
APP = "rounded=1;whiteSpace=wrap;html=1;arcSize=2;fillColor=#fcfdff;strokeColor=#1b2533;strokeWidth=2;verticalAlign=top;align=left;spacingLeft=12;spacingTop=8;fontSize=14;fontStyle=1;"
LAYER = "rounded=1;whiteSpace=wrap;html=1;arcSize=4;strokeColor=none;verticalAlign=top;align=left;spacingLeft=10;spacingTop=4;fontSize=10;fontStyle=1;fontColor=#6b7787;"
TEXT = "text;html=1;align=left;verticalAlign=top;whiteSpace=wrap;"
HDR = TEXT + "fontSize=11;fontStyle=1;fontColor=#6b7787;"
ARROW = "shape=singleArrow;whiteSpace=wrap;html=1;arrowWidth=0.45;arrowSize=0.45;fillColor=#c3cad5;strokeColor=none;"


def box(label, style, x, y, w, h):
    _n[0] += 1
    cid = f"c{_n[0]}"
    cells.append(f'<mxCell id="{cid}" value={quoteattr(label)} style={quoteattr(style)} vertex="1" parent="1">'
                 f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
    return cid


def card(title, body, tags=()):
    t = "".join(f'&nbsp;<span style="font-size:9px;font-weight:bold;padding:0 4px;border-radius:6px;'
                f'background:{bg};color:{fg};">{txt}</span>' for txt, bg, fg in tags)
    return f'<b>{title}</b><br><span style="color:#5b6675;font-size:11px;">{body}</span>' + (f"<br>{t}" if t else "")


DEV = ("DEV", "#fff4e5", "#8a4b00")


def prod(text):
    return (f"PROD: {text}", "#e8f0fe", "#073f91")


# ---------------------------------------------------------------- title
box('<span style="font-size:22px;font-weight:bold;">CorpOne Corporate Portal · architecture</span><br>'
    '<span style="font-size:12px;color:#6b7787;">Corporate bulk-line sign-up portal, back office and partner gateway · '
    'runtime as deployed in the test environment (production differences tagged)</span>', TEXT, 30, 15, 1200, 55)

# ---------------------------------------------------------------- clients
box("CLIENTS", HDR, 30, 82, 200, 20)
box(card("Account officer", "Browser · sign-up portal<br>email one-time code, session cookie"), USER, 30, 105, 200, 80)
box(card("CorpOne back office", "Browser · admin page<br>X-Admin-Token"), USER, 30, 197, 200, 70)
box(card("Partner systems", "Dealer CRM, ops tools<br>API key + scopes"), USER, 30, 279, 200, 70)
box(card("AI agent", "Claude via <b>mcp_server</b><br>tools: list_plans, validate_uen, check_blacklist, "
         "inventory_summary, get_account_status, retry_provisioning"), USER, 30, 361, 200, 125)
box("", ARROW, 236, 380, 30, 34)

# ---------------------------------------------------------------- edge
box("EDGE", HDR, 272, 82, 190, 20)
box(card("CloudFront", "HTTPS<br>https://&lt;ide&gt;.cloudfront.net"), EDGE, 272, 105, 190, 70)
box('<span style="font-size:11px;color:#8a95a5;">forwards to</span>', TEXT + "align=center;", 272, 178, 190, 20)
box(card("nginx :80", "/ to IDE :8080<br>/ports/8501/ to app :8501"), EDGE, 272, 200, 190, 70)
box(card("Production", "Load balancer / CDN over HTTPS, cookies Secure", [("PROD", "#e8f0fe", "#073f91")]), EDGE, 272, 570, 190, 85)
box("", ARROW, 468, 380, 30, 34)

# ---------------------------------------------------------------- app
AX, AW = 504, 880
box('corpone-portal · FastAPI on uvicorn :8501<span style="font-weight:normal;font-size:11px;color:#6b7787;">'
    '&nbsp;&nbsp;&nbsp;1 process · 1 event loop · sync routes in thread pool</span>', APP, AX, 95, AW, 560)
LX, LW = AX + 14, AW - 28


def layer(title, y, h, fill, x=LX, w=LW):
    box(title, LAYER + f"fillColor={fill};", x, y, w, h)


def row(items, y, h, style=SVC, x0=LX + 10, width=LW - 20, gap=10):
    w = (width - gap * (len(items) - 1)) / len(items)
    for i, (t, b) in enumerate(items):
        box(f'<b>{t}</b><br>{b}', style, x0 + i * (w + gap), y, w, h)


layer("MIDDLEWARE (EVERY REQUEST)", 130, 78, "#f1f3f6")
row([("RequestLog", "one line per call, X-Request-ID"), ("NoStoreApi", "API responses never cached"),
     ("PortProxyPrefix", "/ports/&lt;n&gt; path prefix")], 152, 46)
layer("WEB UI (STATIC, FINGERPRINTED JS/CSS, PAGES NO-STORE)", 216, 84, "#eef6ff")
row([("Sign-up portal", "login · company · account officer · plan &amp; lines · review · status"),
     ("Back office", "categories · uploads · inventory lists · orders &amp; carts · merchants · blacklist · API keys · monitoring")],
    238, 52)
layer("APIS", 308, 104, "#f4f1ff")
row([("/api/auth", "OTP request / verify, session"),
     ("/api/signups", "company lookup, steps, SIM category, SIM upload, cart, submit"),
     ("/api/admin", "inventory, uploads, lists, categories, orders, merchants, blacklist"),
     ("/gateway/v1", "partners &amp; agents: keys, scopes, rate limit, audit")], 330, 72)
layer("SERVICES (BUSINESS RULES)", 420, 112, "#f7f8fa")
row([("inventory", "upload, pair on SIM upload, lists, upload history"), ("categories", "per company UEN or all companies"),
     ("orders", "submit, provision, eSIM QR"), ("validators · blacklist", "UEN, NRIC/FIN, ICCID, MSISDN"),
     ("email · storage · ai", "codes &amp; QRs, contracts, contract review")], 442, 80)
HW = (LW - 12) / 2
layer("AFTER SUBMIT / UPLOAD", 540, 102, "#fff8f0", LX, HW)
row([("BackgroundTasks", "provisioning, eSIM QR email, AI contract review"),
     ("Outbound gateway", "retries + backoff, circuit breaker, rate limit, call log")], 562, 70, SVC, LX + 10, HW - 20)
layer("IN MEMORY · RESET ON RESTART", 540, 102, "#fff5f5", LX + HW + 12, HW)
row([("Sign-in limit", "per IP"), ("Rate limit", "per API key"), ("Breakers", "network, billing, ai")],
    562, 70, MEM, LX + HW + 22, HW - 20)
box("", ARROW, AX + AW + 6, 380, 30, 34)

# ---------------------------------------------------------------- data & external
DX, DW = AX + AW + 42, 260
box("DATA", HDR, DX, 82, DW, 20)
box(card("Database", "SQLite data/corpone.db · inventory, categories, uploads, sign-ups, carts, companies, accounts, "
         "lines, sessions, logs", [DEV, prod("RDS Postgres + migrations")]), DISK, DX, 105, DW, 112)
box(card("Contracts", "data/uploads/", [DEV, prod("S3 + SSE")]), DISK, DX, 227, DW, 64)
box(card("Email", "data/outbox/*.eml (codes, eSIM QRs)", [DEV, prod("SES / SMTP")]), DISK, DX, 301, DW, 64)
box(card("Logs", "data/server.log · corpone.api"), DISK, DX, 375, DW, 50)
box("EXTERNAL (VIA OUTBOUND GATEWAY)", HDR, DX, 440, DW, 20)
box(card("Network API", "line activation · mock now"), EXT, DX, 463, DW, 52)
box(card("Billing API", "accounts, subscriptions · mock now"), EXT, DX, 525, DW, 52)
box(card("Claude", "Anthropic API / Bedrock · disabled now"), EXT, DX, 587, DW, 60)

# ---------------------------------------------------------------- legend
lx = 30
for label, style in (("Clients", USER), ("Edge", EDGE), ("App components", SVC), ("In-memory state", MEM),
                     ("Stored data", DISK), ("External systems", EXT)):
    box("", style + "arcSize=20;", lx, 682, 16, 16)
    box(f'<span style="font-size:12px;color:#3c4858;">{label}</span>', TEXT, lx + 20, 679, 140, 22)
    lx += 150

xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<mxfile host="Electron" agent="corpone-docs-generator" version="24.7.0">'
       '<diagram id="architecture" name="Architecture"><mxGraphModel dx="1600" dy="900" grid="1" gridSize="10" guides="1" '
       'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1720" pageHeight="720" math="0" shadow="0">'
       "<root>" + "".join(cells) + "</root></mxGraphModel></diagram></mxfile>\n")
open(OUT, "w", encoding="utf-8").write(xml)
print(f"wrote {OUT}: {len(cells) - 2} cells")
