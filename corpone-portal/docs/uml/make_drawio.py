"""Generate docs/uml/signup-uml.drawio: UML activity, sequence and state diagrams of the CorpOne sign-up.

Every element is a plain draw.io cell, so the file opens and edits normally in draw.io / diagrams.net
(desktop, web, or the VS Code "Draw.io Integration" extension).
"""
import sys
from xml.sax.saxutils import quoteattr

OUT = sys.argv[1]

# ---------------------------------------------------------------- styles
ACT = "rounded=1;whiteSpace=wrap;html=1;arcSize=30;fillColor=#ffffff;strokeColor=#1b2533;fontSize=12;"
SYS = "rounded=1;whiteSpace=wrap;html=1;arcSize=30;fillColor=#e8f0fe;strokeColor=#0a58ca;fontColor=#073f91;fontSize=12;"
ERR = "rounded=1;whiteSpace=wrap;html=1;arcSize=30;fillColor=#fdecea;strokeColor=#c62828;fontColor=#7f1d1d;fontSize=12;"
DEC = "rhombus;whiteSpace=wrap;html=1;fillColor=#fff4e5;strokeColor=#c77700;fontColor=#6b3f00;fontSize=11;"
START = "ellipse;html=1;fillColor=#1b2533;strokeColor=#1b2533;"
FINAL = "ellipse;html=1;shape=doubleEllipse;fillColor=#1b2533;strokeColor=#1b2533;"
FINAL_LBL = "ellipse;whiteSpace=wrap;html=1;shape=doubleEllipse;fillColor=#e7f6ec;strokeColor=#1e7e34;fontColor=#14532d;fontSize=11;fontStyle=1;"
FINAL_BAD = "ellipse;whiteSpace=wrap;html=1;shape=doubleEllipse;fillColor=#fdecea;strokeColor=#c62828;fontColor=#7f1d1d;fontSize=11;fontStyle=1;"
GROUP = "rounded=1;whiteSpace=wrap;html=1;arcSize=3;fillColor=#f7f8fa;strokeColor=#cfd6e2;dashed=1;verticalAlign=top;align=left;spacingLeft=10;spacingTop=6;fontStyle=1;fontSize=13;fontColor=#3c4858;"
EDGE = "edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;endFill=1;strokeColor=#6b7787;fontSize=11;labelBackgroundColor=#ffffff;"
EDGE_DASH = EDGE + "dashed=1;"
TITLE = "text;html=1;align=left;verticalAlign=top;fontSize=18;fontStyle=1;"
NOTE = "shape=note;whiteSpace=wrap;html=1;size=12;fillColor=#fff9c4;strokeColor=#c9b458;fontSize=11;align=left;spacingLeft=6;"

STATE = "rounded=1;whiteSpace=wrap;html=1;arcSize=40;fillColor=#ffffff;strokeColor=#1b2533;fontSize=12;fontStyle=1;"
STATE_OK = STATE + "fillColor=#e7f6ec;strokeColor=#1e7e34;fontColor=#14532d;"
STATE_BAD = STATE + "fillColor=#fdecea;strokeColor=#c62828;fontColor=#7f1d1d;"
STATE_ON = STATE + "fillColor=#e8f0fe;strokeColor=#0a58ca;fontColor=#073f91;"


class Page:
    def __init__(self, name, width, height):
        self.name, self.width, self.height = name, width, height
        self.cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
        self.n = 0

    def _id(self, prefix):
        self.n += 1
        return f"{prefix}{self.n}"

    def node(self, label, style, x, y, w, h, cid=None):
        cid = cid or self._id("n")
        self.cells.append(f'<mxCell id="{cid}" value={quoteattr(label)} style={quoteattr(style)} vertex="1" parent="1">'
                          f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
        return cid

    def edge(self, src, dst, label="", style=EDGE, points=(), exit=None, entry=None):
        cid = self._id("e")
        st = style
        if exit:
            st += f"exitX={exit[0]};exitY={exit[1]};exitDx=0;exitDy=0;"
        if entry:
            st += f"entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;"
        pts = "".join(f'<mxPoint x="{px}" y="{py}"/>' for px, py in points)
        pts = f'<Array as="points">{pts}</Array>' if pts else ""
        self.cells.append(f'<mxCell id="{cid}" value={quoteattr(label)} style={quoteattr(st)} edge="1" parent="1" source="{src}" target="{dst}">'
                          f'<mxGeometry relative="1" as="geometry">{pts}</mxGeometry></mxCell>')
        return cid

    def free_edge(self, x1, y1, x2, y2, label="", style=EDGE):
        cid = self._id("m")
        self.cells.append(f'<mxCell id="{cid}" value={quoteattr(label)} style={quoteattr(style)} edge="1" parent="1">'
                          f'<mxGeometry relative="1" as="geometry"><mxPoint x="{x1}" y="{y1}" as="sourcePoint"/>'
                          f'<mxPoint x="{x2}" y="{y2}" as="targetPoint"/></mxGeometry></mxCell>')

    def xml(self, idx):
        return (f'<diagram id="page{idx}" name={quoteattr(self.name)}><mxGraphModel dx="1400" dy="900" grid="1" gridSize="10" '
                f'guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
                f'pageWidth="{self.width}" pageHeight="{self.height}" math="0" shadow="0"><root>'
                + "".join(self.cells) + "</root></mxGraphModel></diagram>")


# ================================================================ 1. Activity diagram
def activity():
    p = Page("1 · Activity: sign-up flow", 1100, 3120)
    p.node("CorpOne corporate sign-up · UML activity diagram", TITLE, 20, 10, 700, 30)
    MX, W, H = 300, 240, 50          # main column
    EX = 640                         # error column
    DW, DH = 170, 90                 # decision size
    dx = MX + (W - DW) / 2

    def main(label, y, style=ACT, h=H):
        return p.node(label, style, MX, y, W, h)

    def dec(label, y):
        return p.node(label, DEC, dx, y, DW, DH)

    def err(label, y):
        return p.node(label, ERR, EX, y, 180, 44)

    # step frames (drawn first, behind)
    frames = [("Step 1 · Sign in", 60, 470), ("Step 2 · Company", 540, 730), ("Step 3 · Account officer", 1280, 210),
              ("Step 4 · Plan & lines", 1500, 750), ("Step 5 · Review & submit", 2260, 240), ("Step 6 · Provisioning", 2510, 540)]
    for t, y, h in frames:
        p.node(t, GROUP, 40, y, 1000, h)

    s = p.node("", START, MX + W / 2 - 15, 70, 30, 30)
    a1 = main("Enter work email", 120)
    a2 = main("Email 6-digit code<br>(HMAC stored, 5 min)", 200, SYS)
    a3 = main("Enter code", 280)
    d1 = dec("Code valid?", 350)
    a4 = main("Session cookie, 8 h", 470, SYS)
    p.edge(s, a1); p.edge(a1, a2); p.edge(a2, a3); p.edge(a3, d1)
    p.edge(d1, a3, "no, under 5 tries", exit=(1, 0.5), entry=(1, 0.5), points=[(MX + W + 60, 395), (MX + W + 60, 305)])
    p.edge(d1, a1, "no, 5 tries or expired", exit=(0, 0.5), entry=(0, 0.5), points=[(MX - 60, 395), (MX - 60, 145)])
    p.edge(d1, a4, "yes")

    b1 = main("Enter UEN<br>(lookup when the field is left)", 560)
    d2 = dec("UEN valid and<br>not blacklisted?", 640)
    e1 = err("Show reason", 663)
    d3 = dec("Company on record?", 760)
    b2 = p.node("Retrieve name and address<br>(locked if it has an order)", SYS, 520, 870, 220, 50)
    b3 = p.node("Enter name and address", ACT, 120, 870, 200, 50)
    b4 = main("Validate UEN<br>DRAFT sign-up created", 950, SYS)
    b5 = main("Upload signed contract PDF", 1030)
    d4 = dec("PDF, 10 MB<br>or less?", 1100)
    e2 = err("Show reason", 1123)
    p.edge(a4, b1); p.edge(b1, d2)
    p.edge(d2, e1, "no", exit=(1, 0.5), entry=(0, 0.5))
    p.edge(e1, b1, "", exit=(0.5, 0), entry=(1, 0.5), points=[(EX + 90, 585)])
    p.edge(d2, d3, "yes")
    p.edge(d3, b2, "yes", exit=(1, 0.5), entry=(0.5, 0))
    p.edge(d3, b3, "no", exit=(0, 0.5), entry=(0.5, 0))
    p.edge(b2, b4, "", exit=(0.5, 1), entry=(1, 0.5))
    p.edge(b3, b4, "", exit=(0.5, 1), entry=(0, 0.5))
    p.edge(b4, b5); p.edge(b5, d4)
    p.edge(d4, e2, "no", exit=(1, 0.5), entry=(0, 0.5))
    p.edge(e2, b5, "", exit=(0.5, 0), entry=(1, 0.5), points=[(EX + 90, 1055)])

    c0 = main("Store contract<br>(optional AI review in background)", 1205, SYS)
    p.edge(d4, c0, "yes")
    O = 60   # Steps 3-6 sit lower so the Step 2 frame can hold "Store contract"
    c1 = main("Account officer details<br>and merchant code (drop-down)", 1240 + O)
    d5 = dec("NRIC/FIN valid, not<br>blacklisted, merchant active?", 1310 + O)
    e3 = err("Show reason", 1333 + O)
    p.edge(c0, c1); p.edge(c1, d5)
    p.edge(d5, e3, "no", exit=(1, 0.5), entry=(0, 0.5))
    p.edge(e3, c1, "", exit=(0.5, 0), entry=(1, 0.5), points=[(EX + 90, 1265 + O)])

    O = 120
    q1 = main("Select SIM category", 1410 + O)
    d6 = dec("Allowed for<br>this UEN?", 1480 + O)
    e4 = err("Choose another", 1503 + O)
    d7 = dec("Upload SIMs?", 1600 + O)
    q2 = p.node("Upload SIM CSV<br>auto-pair with available numbers", SYS, 560, 1710 + O, 220, 50)
    q3 = main("See numbers available<br>and lines ready", 1790 + O, SYS)
    q4 = main("Pick plan, SIM type, quantity", 1870 + O)
    d8 = dec("Enough paired<br>lines?", 1940 + O)
    e5 = err("Show how many are ready", 1963 + O)
    q5 = main("Lines RESERVED in cart", 2050 + O, SYS)
    p.edge(d5, q1, "yes"); p.edge(q1, d6)
    p.edge(d6, e4, "no", exit=(1, 0.5), entry=(0, 0.5))
    p.edge(e4, q1, "", exit=(0.5, 0), entry=(1, 0.5), points=[(EX + 90, 1435 + O)])
    p.edge(d6, d7, "yes")
    p.edge(d7, q2, "yes", exit=(1, 0.5), entry=(0.5, 0))
    p.edge(q2, q3, "", exit=(0.5, 1), entry=(1, 0.5))
    p.edge(d7, q3, "no")
    p.edge(q3, q4); p.edge(q4, d8)
    p.edge(d8, e5, "no", exit=(1, 0.5), entry=(0, 0.5))
    p.edge(e5, q4, "", exit=(0.5, 0), entry=(1, 0.5), points=[(EX + 90, 1895 + O)])
    p.edge(d8, q5, "yes")
    p.edge(q5, q4, "add more", exit=(0, 0.5), entry=(0, 0.5), points=[(MX - 60, 2075 + O), (MX - 60, 1895 + O)])

    r1 = main("Review and confirm", 2170 + O)
    d9 = dec("Still not blacklisted,<br>cart complete?", 2240 + O)
    e6 = err("Show reason", 2263 + O)
    p.edge(q5, r1, "done")
    p.edge(r1, d9)
    p.edge(d9, e6, "no", exit=(1, 0.5), entry=(0, 0.5))
    p.edge(e6, r1, "", exit=(0.5, 0), entry=(1, 0.5), points=[(EX + 90, 2195 + O)])

    v0 = main("Submit: Company, AO, Account, Lines<br>inventory ASSIGNED", 2400 + O, SYS, 54)
    v1 = main("Provisioning (background):<br>billing account, then each line:<br>network activate, billing subscription", 2480 + O, SYS, 70)
    v2 = main("Email eSIM QR codes<br>to the verified AO email", 2580 + O, SYS)
    d10 = dec("All lines<br>provisioned?", 2660 + O)
    f1 = p.node("COMPLETED", FINAL_LBL, MX + W / 2 - 55, 2800 + O, 110, 60)
    f2 = p.node("PARTIALLY<br>FAILED", FINAL_BAD, EX, 2800 + O, 110, 60)
    f3 = p.node("FAILED", FINAL_BAD, EX + 160, 2800 + O, 110, 60)
    p.edge(d9, v0, "yes"); p.edge(v0, v1); p.edge(v1, v2); p.edge(v2, d10)
    p.edge(d10, f1, "yes")
    p.edge(d10, f2, "some", exit=(1, 0.5), entry=(0.5, 0))
    p.edge(d10, f3, "none", exit=(1, 0.5), entry=(0.5, 0), points=[(EX + 215, 2705 + O)])
    # retry: from below each failed end state, round the right side, back into provisioning
    p.edge(f2, v1, "", EDGE_DASH, exit=(0.5, 1), entry=(1, 0.5), points=[(EX + 55, 2890 + O), (990, 2890 + O), (990, 2515 + O)])
    p.edge(f3, v1, "back office: retry provisioning", EDGE_DASH, exit=(0.5, 1), entry=(1, 0.5),
           points=[(EX + 215, 2890 + O), (990, 2890 + O), (990, 2515 + O)])
    p.node("Legend: white = account officer action · blue = system action · orange = decision · red = error / failed",
           "text;html=1;align=left;fontSize=11;fontColor=#5b6675;", 40, 2950 + O, 800, 20)
    return p


# ================================================================ 2. Sequence diagram
def sequence():
    parts = [("AO", "Account officer", True), ("UI", "Portal UI (browser)", False), ("API", "Portal API", False),
             ("DB", "Database", False), ("ST", "Contract store (S3)", False), ("EM", "Email (SES / SMTP)", False),
             ("GW", "Outbound gateway", False), ("BI", "Billing API", False), ("NW", "Network API", False)]
    msgs = [
        ("note", "Step 1 · Sign in"),
        ("AO", "UI", "work email"), ("UI", "API", "POST /api/auth/otp/request"), ("API", "DB", "OtpChallenge (HMAC of code)"),
        ("API", "EM", "6-digit code"), ("AO", "UI", "code"), ("UI", "API", "POST /api/auth/otp/verify"),
        ("API", "DB", "AoSession (SHA-256 of token)"), ("API", "UI", "Set-Cookie corpone_session", True),
        ("note", "Step 2 · Company"),
        ("AO", "UI", "UEN (leaves the field)"), ("UI", "API", "GET /api/companies/{uen}"), ("API", "DB", "blacklist, Company lookup"),
        ("API", "UI", "existing / customer, name, address", True), ("UI", "API", "POST /api/signups"),
        ("API", "DB", "Company (if new), Signup DRAFT"), ("UI", "API", "POST /api/signups/{id}/contract"), ("API", "ST", "PutObject (SSE)"),
        ("note", "Step 3 · Account officer"),
        ("UI", "API", "GET /api/merchants"), ("UI", "API", "PUT /api/signups/{id}/account-officer"),
        ("API", "DB", "NRIC/FIN, blacklist, merchant checks"),
        ("note", "Step 4 · Plan & lines"),
        ("UI", "API", "GET /api/signups/{id}/categories"), ("UI", "API", "PUT /api/signups/{id}/category"),
        ("UI", "API", "POST /api/signups/{id}/sims (optional)"), ("API", "DB", "Sims, pair with Numbers: PAIRED; UploadBatch"),
        ("UI", "API", "GET /api/signups/{id}/availability"), ("UI", "API", "POST /api/signups/{id}/cart"),
        ("API", "DB", "CartLines; SIM + number RESERVED"),
        ("note", "Step 5 · Review & submit"),
        ("UI", "API", "POST /api/signups/{id}/submit"),
        ("API", "DB", "re-check; Company, AO, Account, Lines; ASSIGNED"), ("API", "UI", "account number", True),
        ("note", "Step 6 · Provisioning (background task)"),
        ("API", "GW", "create billing account"), ("GW", "BI", "POST (Idempotency-Key account-id)"),
        ("API", "GW", "per line: activate line"), ("GW", "NW", "POST (Idempotency-Key line-id-network)"),
        ("API", "GW", "per line: add subscription"), ("GW", "BI", "POST (Idempotency-Key line-id-billing)"),
        ("API", "EM", "eSIM QR codes to the verified AO email"), ("API", "DB", "Signup COMPLETED / PARTIALLY_FAILED / FAILED"),
        ("UI", "API", "GET /api/accounts/{account number} (poll)"),
    ]
    gap, top, step = 175, 70, 36
    height = top + 80 + step * (len(msgs) + 1)
    p = Page("2 · Sequence: sign-up", 120 + gap * len(parts), height + 60)
    p.node("CorpOne corporate sign-up · UML sequence diagram", TITLE, 20, 10, 800, 30)
    x = {}
    for i, (key, name, actor) in enumerate(parts):
        cx = 100 + i * gap
        x[key] = cx
        if actor:
            st = "shape=umlLifeline;participant=umlActor;perimeter=lifelinePerimeter;whiteSpace=wrap;html=1;container=0;collapsible=0;size=50;outlineConnect=0;fontSize=12;"
        else:
            st = "shape=umlLifeline;perimeter=lifelinePerimeter;whiteSpace=wrap;html=1;container=0;collapsible=0;size=40;outlineConnect=0;fillColor=#ffffff;strokeColor=#1b2533;fontSize=12;"
        p.node(name, st, cx - 70, top, 140, height - top)
    y = top + 70
    num = 0
    for m in msgs:
        if m[0] == "note":
            p.node(m[1], "rounded=0;whiteSpace=wrap;html=1;fillColor=#fff4b3;strokeColor=#d6c35a;fontStyle=1;fontSize=12;",
                   x["AO"] - 60, y - 12, x["NW"] - x["AO"] + 120, 24)
            y += step
            continue
        a, b, label = m[0], m[1], m[2]
        reply = len(m) > 3
        num += 1
        style = ("html=1;verticalAlign=bottom;endArrow=open;dashed=1;endSize=8;fontSize=11;strokeColor=#6b7787;" if reply
                 else "html=1;verticalAlign=bottom;endArrow=block;endFill=1;fontSize=11;strokeColor=#1b2533;")
        p.free_edge(x[a], y, x[b], y, f"{num}. {label}", style)
        y += step
    return p


# ================================================================ 3. Order status state machine
def order_state():
    p = Page("3 · State: order status", 1300, 520)
    p.node("Order (sign-up) status · UML state diagram", TITLE, 20, 10, 700, 30)
    s = p.node("", START, 40, 225, 30, 30)
    draft = p.node("DRAFT", STATE, 220, 215, 130, 50)
    sub = p.node("SUBMITTED", STATE_ON, 520, 215, 140, 50)
    ok = p.node("COMPLETED", STATE_OK, 1030, 215, 140, 50)
    part = p.node("PARTIALLY_FAILED", STATE_BAD, 770, 330, 170, 50)
    fail = p.node("FAILED", STATE_BAD, 770, 430, 170, 50)
    end = p.node("", FINAL, 1230, 225, 30, 30)
    p.edge(s, draft, "POST /api/signups<br>(UEN validated)")
    p.edge(draft, draft, "contract, AO, SIM category,<br>cart changes", exit=(0.75, 0), entry=(0.25, 0),
           points=[(317, 160), (252, 160)])
    p.edge(draft, sub, "POST /submit<br>(checks pass)")
    p.edge(sub, ok, "every line provisioned")
    p.edge(sub, part, "some lines failed", exit=(0.5, 1), entry=(0, 0.5))
    p.edge(sub, fail, "billing account or<br>all lines failed", exit=(0.25, 1), entry=(0, 0.5))
    p.edge(part, ok, "back office retry<br>succeeds", exit=(1, 0.5), entry=(0.5, 1))
    p.edge(fail, ok, "back office retry succeeds", exit=(1, 0.5), entry=(0.75, 1))
    p.edge(ok, end)
    p.node("Retry: POST /api/admin/accounts/{account_number}/retry-provisioning (same idempotency keys)", NOTE, 220, 330, 330, 50)
    return p


# ================================================================ 4. SIM + number state machine
def line_state():
    p = Page("4 · State: SIM + number", 1300, 360)
    p.node("Each SIM + number pair · UML state diagram", TITLE, 20, 10, 700, 30)
    s = p.node("", START, 40, 165, 30, 30)
    av = p.node("AVAILABLE", STATE, 190, 155, 140, 50)
    pa = p.node("PAIRED", STATE_ON, 520, 155, 140, 50)
    re = p.node("RESERVED", STATE, 800, 155, 140, 50)
    asg = p.node("ASSIGNED", STATE_OK, 1060, 155, 140, 50)
    end = p.node("", FINAL, 1240, 165, 30, 30)
    p.edge(s, av, "uploaded")
    p.edge(av, pa, "SIM upload pairs SIM + number<br>(same category)")
    p.edge(pa, re, "added to a cart", exit=(1, 0.3), entry=(0, 0.3))
    p.edge(re, pa, "removed from the cart", exit=(0, 0.7), entry=(1, 0.7))
    p.edge(re, asg, "order submitted")
    p.edge(asg, end)
    p.node("A number upload only adds numbers as AVAILABLE; pairing happens on SIM upload: that file's SIMs first, "
           "then numbers left over go to earlier waiting SIMs.", NOTE, 190, 250, 560, 50)
    return p


pages = [activity(), sequence(), order_state(), line_state()]
xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<mxfile host="Electron" agent="corpone-docs-generator" version="24.7.0">'
       + "".join(pg.xml(i + 1) for i, pg in enumerate(pages)) + "</mxfile>\n")
open(OUT, "w", encoding="utf-8").write(xml)
print(f"wrote {OUT}: {len(pages)} pages, {sum(len(pg.cells) - 2 for pg in pages)} cells")
