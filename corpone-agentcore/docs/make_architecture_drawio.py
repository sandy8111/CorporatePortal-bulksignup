"""Generate docs/architecture_agentcore.drawio: corpone-agentcore architecture with the numbered request flow.

Usage: python3 docs/make_architecture_drawio.py docs/architecture_agentcore.drawio
Once edited by hand in draw.io, treat the .drawio file as the source and stop regenerating it.
"""
import sys
from xml.sax.saxutils import quoteattr

OUT = sys.argv[1]
cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
_n = [0]

BASE = "rounded=1;whiteSpace=wrap;html=1;arcSize=6;align=left;verticalAlign=top;spacingLeft=8;spacingTop=5;spacingRight=6;fontSize=11;"
USER = BASE + "fillColor=#e8f0fe;strokeColor=#0a58ca;strokeWidth=1.5;"
AC = BASE + "fillColor=#fff4e5;strokeColor=#c77700;strokeWidth=1.5;"          # AgentCore services
AWS = BASE + "fillColor=#f4f1ff;strokeColor=#6d4bd1;strokeWidth=1.5;"         # other AWS services
APP = BASE + "fillColor=#e7f6ec;strokeColor=#1e7e34;strokeWidth=1.5;"         # CorpOne code
OBS = BASE + "fillColor=#f1f3f6;strokeColor=#6b7787;strokeWidth=1.5;"
FRAME = ("rounded=1;whiteSpace=wrap;html=1;arcSize=1;fillColor=none;dashed=1;strokeWidth=1.5;verticalAlign=top;"
         "align=left;spacingLeft=12;spacingTop=6;fontSize=13;fontStyle=1;")
TEXT = "text;html=1;align=left;verticalAlign=top;whiteSpace=wrap;"
FLOW = ("edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;endFill=1;strokeColor=#1b2533;strokeWidth=1.6;"
        "fontSize=10;labelBackgroundColor=#ffffff;")
DASH = FLOW + "dashed=1;strokeColor=#6b7787;"


def box(label, style, x, y, w, h):
    _n[0] += 1
    cid = f"c{_n[0]}"
    cells.append(f'<mxCell id="{cid}" value={quoteattr(label)} style={quoteattr(style)} vertex="1" parent="1">'
                 f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
    return cid


def link(a, b, label="", style=FLOW, exit=None, entry=None, points=()):
    _n[0] += 1
    st = style
    if exit:
        st += f"exitX={exit[0]};exitY={exit[1]};exitDx=0;exitDy=0;"
    if entry:
        st += f"entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;"
    pts = "".join(f'<mxPoint x="{px}" y="{py}"/>' for px, py in points)
    pts = f'<Array as="points">{pts}</Array>' if pts else ""
    cells.append(f'<mxCell id="e{_n[0]}" value={quoteattr(label)} style={quoteattr(st)} edge="1" parent="1" '
                 f'source="{a}" target="{b}"><mxGeometry relative="1" as="geometry">{pts}</mxGeometry></mxCell>')


def card(title, body):
    return f'<b>{title}</b><br><span style="color:#4b5563;">{body}</span>'


def num(n, text):
    return (f'<span style="background:#1b2533;color:#ffffff;font-weight:bold;">&nbsp;{n}&nbsp;</span>&nbsp;{text}')


def key(text):
    return f'<br><span style="color:#7c4a03;font-weight:bold;">KEY</span> <span style="color:#7c4a03;">{text}</span>'


# ---------------------------------------------------------------- title
box('<span style="font-size:22px;font-weight:bold;">corpone-agentcore · architecture</span><br>'
    '<span style="font-size:12px;color:#6b7787;">CorpOne assistant on Amazon Bedrock AgentCore · numbered arrows = one '
    'assistant request · KEY = credential used on that hop · region ap-southeast-1 (Singapore)</span>', TEXT, 30, 12, 1500, 52)

# ---------------------------------------------------------------- frames
box("Users", FRAME + "strokeColor=#0a58ca;fontColor=#0a58ca;", 20, 80, 240, 640)
box("AWS account · ap-southeast-1", FRAME + "strokeColor=#c77700;fontColor=#8a4b00;", 340, 80, 1080, 1000)
agentcore_frame = box("Amazon Bedrock AgentCore", FRAME + "strokeColor=#c77700;fontColor=#c77700;dashed=0;strokeWidth=1;", 370, 230, 690, 450)
portal_frame = box("CorpOne portal (hosted in AWS, HTTPS)", FRAME + "strokeColor=#1e7e34;fontColor=#1e7e34;", 1120, 230, 280, 580)
box("Observability and evaluation", FRAME + "strokeColor=#6b7787;fontColor=#3c4858;dashed=0;strokeWidth=1;", 370, 840, 1030, 220)

# ---------------------------------------------------------------- users
box(card("Account officer", "Cognito group <b>officer</b> (default)<br>tools: listPlans, validateUen"), USER, 40, 120, 200, 74)
box(card("CorpOne ops", "Cognito group <b>ops</b><br>all 6 tools; retry needs \"yes, retry\""), USER, 40, 210, 200, 74)
box('<span style="font-size:10px;color:#4b5563;">both use the chat client below</span>', TEXT + "align=center;", 40, 288, 200, 20)
client = box(card("Chat client / portal widget", "signs the user in, then calls the Runtime with the user's access "
                  "token and a session id"), USER, 40, 360, 200, 110)
box(card("Engineer (deploy / CI)", "infra/deploy.py (dry run, --apply)<br>agent/build_and_push.sh<br>"
         "evals/run_offline_eval.py --batch"), USER, 40, 590, 200, 96)

# ---------------------------------------------------------------- other AWS services
cognito = box(card("Amazon Cognito", "user pool, groups officer / ops<br>SRP sign-in, 60-minute access tokens") +
              key("access token: sub, client_id, cognito:groups"), AWS, 400, 110, 240, 96)
box(card("Amazon ECR", "corpone-assistant image (linux/arm64), scan on push<br>pushed by agent/build_and_push.sh, "
         "pulled by the Runtime"), AWS, 1140, 110, 250, 96)
bedrock = box(card("Amazon Bedrock", "model MODEL_ID (e.g. Claude)") + key("Runtime IAM role: bedrock:InvokeModel"),
              AWS, 400, 720, 240, 82)

# ---------------------------------------------------------------- AgentCore
policy = box(card("AgentCore Policy (Cedar)", "10 everyone: listPlans, validateUen<br>20 ops: inventory, blacklist, accounts<br>"
                  "30 ops: retryProvisioning (CA…)<br>40 forbid retry unless ops<br>LOG_ONLY, then ENFORCE · kill switch"),
             AC, 800, 252, 240, 118)
runtime = box(card("AgentCore Runtime", "<b>corpone_assistant</b> (Strands)<br>JWT authorizer (Cognito)<br>"
                   "Authorization header passed through<br>tools and prompt from cognito:groups"), AC, 400, 410, 240, 112)
gateway = box(card("AgentCore Gateway (MCP)", "JWT authorizer (same Cognito client)<br>target <b>CorpOnePortal</b>: "
                   "6 tools from portal code<br>semantic tool search · uploads excluded<br>"
                   '<span style="font-size:10px;">role: AuthorizeAction, GetPolicyEngine, GetResourceApiKey</span>'),
              AC, 800, 410, 240, 112)
memory = box(card("AgentCore Memory", "short-term events only<br>NRIC/FIN and emails masked<br>7-day expiry · actor = token sub"),
             AC, 400, 570, 240, 86)
identity = box(card("AgentCore Identity", "token vault: portal API key (cpk_…)<br>API-key credential provider") +
               key("never in agent code or env"), AC, 800, 570, 240, 86)

# ---------------------------------------------------------------- portal
gw_api = box(card("/gateway/v1 (partner API)", "listPlans · validateUen · getInventorySummary · checkBlacklist · "
                  "getAccountStatus · retryProvisioning") + key("X-API-Key: scoped key, rate limit, audit row"),
             APP, 1140, 410, 240, 112)
portal = box(card("Portal and back office", "sign-up wizard, inventory, orders<br>CORPONE_TRUSTED_PROXIES<br>"
                  "OTel spans: request id, caller kind only"), APP, 1140, 570, 240, 86)
db = box(card("Database", "RDS Postgres (prod) · SQLite (dev)"), APP, 1140, 700, 240, 56)
link(gw_api, portal, "", DASH, exit=(0.5, 1), entry=(0.5, 0))
link(portal, db, "", DASH, exit=(0.5, 1), entry=(0.5, 0))

# ---------------------------------------------------------------- observability
evals = box(card("AgentCore Evaluations", "online: 10% of sessions<br>GoalSuccess, ToolSelection, Helpfulness, Correctness<br>"
                 "offline gate: evals/dataset.jsonl (12)"), OBS, 400, 880, 250, 100)
cw = box(card("Amazon CloudWatch", "Transaction Search: aws/spans<br>runtime and gateway logs, policy decisions<br>"
              "GenAI observability"), OBS, 760, 880, 290, 100)
omni = box(card("CloudWatch Omni", "one view of portal + agent traces<br>enterprise SSO (web UI / VS Code)<br>"
                "AI assistant · AWS DevOps Agent investigations"), OBS, 1100, 880, 280, 100)
box('<span style="font-size:10px;color:#6b7787;">ADOT in the agent image · OpenTelemetry in the portal · no prompt text, '
    'token or email on spans</span>', TEXT, 400, 1000, 900, 24)

# ---------------------------------------------------------------- numbered request flow
link(client, cognito, num(1, "sign in (SRP)"), exit=(1, 0.2), entry=(0, 0.5), points=[(300, 382), (300, 158)])
link(client, runtime, num(2, "POST /invocations<br>Bearer token<br>+ session id"), exit=(1, 0.96), entry=(0, 0.5))
link(runtime, memory, num(3, "history / save turn"), exit=(0.5, 1), entry=(0.5, 0))
link(runtime, bedrock, num(4, "model<br>call"), exit=(0, 0.9), entry=(0, 0.5), points=[(355, 511), (355, 761)])
link(runtime, gateway, num(5, "MCP tools<br>same user token"), exit=(1, 0.5), entry=(0, 0.5))
link(gateway, policy, num(6, "authorize every call"), exit=(0.5, 0), entry=(0.5, 1))
link(gateway, identity, num(7, "get API key"), exit=(0.5, 1), entry=(0.5, 0))
link(gateway, gw_api, num(8, "HTTPS<br>X-API-Key"), exit=(1, 0.5), entry=(0, 0.5))
link(cognito, runtime, "JWKS: verify token", DASH, exit=(0.5, 1), entry=(0.5, 0))
link(cognito, gateway, "JWKS", DASH, exit=(1, 0.5), entry=(0, 0.2), points=[(740, 158), (740, 432)])
link(agentcore_frame, cw, num(9, "traces / logs"), DASH, exit=(0.68, 1), entry=(0.5, 0))
link(portal_frame, omni, num(9, "OTLP traces"), DASH, exit=(0.47, 1), entry=(0.5, 0))
link(cw, omni, "", DASH, exit=(1, 0.5), entry=(0, 0.5))
link(cw, evals, num(10, "score<br>sessions"), DASH, exit=(0, 0.5), entry=(1, 0.5))

# ---------------------------------------------------------------- legend
lx = 30
for label, style in (("Users and clients", USER), ("AgentCore", AC), ("Other AWS", AWS), ("CorpOne code", APP),
                     ("Observability", OBS)):
    box("", style + "arcSize=20;", lx, 1100, 16, 16)
    box(f'<span style="font-size:12px;color:#3c4858;">{label}</span>', TEXT, lx + 20, 1097, 150, 22)
    lx += 160
box('<span style="font-size:12px;color:#3c4858;">solid = request path · dashed = supporting / async</span>',
    TEXT, lx, 1097, 400, 22)

xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<mxfile host="Electron" agent="corpone-docs-generator" version="24.7.0">'
       '<diagram id="agentcore-architecture" name="corpone-agentcore"><mxGraphModel dx="1600" dy="900" grid="1" '
       'gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1440" '
       'pageHeight="1140" math="0" shadow="0"><root>' + "".join(cells) + "</root></mxGraphModel></diagram></mxfile>\n")
open(OUT, "w", encoding="utf-8").write(xml)
print(f"wrote {OUT}: {len(cells) - 2} cells")
