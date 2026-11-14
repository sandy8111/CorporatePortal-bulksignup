"""Generate the database schema docs and ERD from the SQLAlchemy models (app/models.py).

Run from corpone-portal/:  .venv/bin/python docs/schema/make_schema_docs.py
Writes, into docs/schema/:
  schema_sqlite.sql    DDL exactly as the app creates it today (SQLite)
  schema_postgres.sql  the same schema compiled for PostgreSQL (production target)
  SCHEMA.md            every table and column, keys, indexes, enums, relationships
  erd.mmd              Mermaid ER diagram (rendered by GitHub)
  erd.drawio           editable draw.io ER diagram
erd.png is an export of erd.drawio (open it in draw.io and File > Export as > PNG after changes).
"""
import os
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

os.environ.setdefault("CORPONE_DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/schema.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import Enum, UniqueConstraint  # noqa: E402
from sqlalchemy.dialects import postgresql, sqlite  # noqa: E402
from sqlalchemy.schema import CreateIndex, CreateTable  # noqa: E402

import app.models  # noqa: E402,F401  (registers the tables)
from app.db import Base  # noqa: E402

OUT = Path(__file__).resolve().parent
T = Base.metadata.tables

GROUPS = [
    ("Inventory", "#e7f6ec", "#1e7e34", ["category_allocations", "number_inventory", "sim_inventory", "upload_batches"]),
    ("Sign-up (draft order)", "#e8f0fe", "#0a58ca", ["signups", "cart_lines"]),
    ("Customer and order", "#fff4e5", "#c77700", ["companies", "account_officers", "corporate_accounts", "lines"]),
    ("Reference data", "#f4f1ff", "#6d4bd1", ["plans", "merchants", "blacklist_entries"]),
    ("Sign-in, gateway and logs", "#f1f3f6", "#6b7787",
     ["otp_challenges", "ao_sessions", "api_clients", "gateway_audit_log", "outbound_call_log"]),
]
assert sorted(t for g in GROUPS for t in g[3]) == sorted(T), "a table is missing from GROUPS"

PURPOSE = {
    "category_allocations": "Who may use a SIM category: one company (uen) or all companies (uen NULL). Inactive hides it.",
    "number_inventory": "Mobile numbers (MSISDN) by category. Added as AVAILABLE; paired by SIM uploads.",
    "sim_inventory": "SIM cards (ICCID) by category and type, with eSIM activation data and the paired number.",
    "upload_batches": "Every SIM or number file uploaded: who, from where, counts and every row's result (rows_json).",
    "signups": "Draft order an account officer fills in step by step; turns into Company / Account / Lines on submit.",
    "cart_lines": "SIM + number + plan reserved in a sign-up's cart. Kept after submit.",
    "companies": "Corporate customer, one per UEN. Saved on first validation; not overwritten after its first order.",
    "account_officers": "Company representative (NRIC/FIN/passport) who manages the lines.",
    "corporate_accounts": "Account created on submit (account number CA…), linked to billing.",
    "lines": "Provisioned mobile line: SIM + number + plan, with network/billing references and status.",
    "plans": "Monthly plans offered (Ray, Flash, Glare). Seeded at startup.",
    "merchants": "Delegated merchant codes shown in the AO step. Seeded with CORP-DIRECT-01.",
    "blacklist_entries": "Blocked companies (UEN) and account officers (NRIC/FIN/passport), with validity dates.",
    "otp_challenges": "Sign-in codes: only an HMAC of the code is stored; expiry, attempts, consumed time.",
    "ao_sessions": "Account officer sessions: only a SHA-256 of the token is stored; expiry and revocation.",
    "api_clients": "Partner / AI-agent API keys for /gateway/v1: prefix + SHA-256 of the secret, scopes, rate limit.",
    "gateway_audit_log": "One row per inbound /gateway/v1 request.",
    "outbound_call_log": "One row per outbound call attempt to network, billing or AI.",
}

# Links used in code but not declared as foreign keys (matched on a shared value).
LOGICAL = [
    ("signups", "category", "category_allocations", "category", "SIM category chosen"),
    ("sim_inventory", "category", "category_allocations", "category", "category"),
    ("number_inventory", "category", "category_allocations", "category", "category"),
    ("category_allocations", "uen", "companies", "uen", "allocated to"),
    ("signups", "uen", "companies", "uen", "company"),
    ("signups", "merchant_code", "merchants", "code", "merchant"),
    ("corporate_accounts", "merchant_code", "merchants", "code", "merchant"),
    ("upload_batches", "signup_id", "signups", "id", "portal upload"),
    ("gateway_audit_log", "client_id", "api_clients", "id", "API client"),
]


def single_unique(t):
    out = {c.name for c in t.columns if c.unique}
    for u in t.constraints:
        if isinstance(u, UniqueConstraint) and len(u.columns) == 1:
            out.add(list(u.columns)[0].name)
    return out


def composite_uniques(t):
    return [[c.name for c in u.columns] for u in t.constraints if isinstance(u, UniqueConstraint) and len(u.columns) > 1]


def coltype(c, dialect=None):
    if isinstance(c.type, Enum):
        return "ENUM"
    return c.type.compile(dialect=dialect or sqlite.dialect())


def default(c):
    d = c.default
    if d is None:
        return ""
    if d.is_scalar:
        v = d.arg
        return getattr(v, "value", v) if not isinstance(v, bool) else str(v).lower()
    return "auto (" + getattr(d.arg, "__name__", "fn").lstrip("_") + ")"


def keys(t, c):
    k = []
    if c.primary_key:
        k.append("PK")
    for fk in c.foreign_keys:
        k.append(f"FK → {fk.target_fullname}")
    if c.name in single_unique(t) and not c.primary_key:
        k.append("UNIQUE")
    if c.index and c.name not in single_unique(t):
        k.append("INDEX")
    return k


# ---------------------------------------------------------------- DDL
def ddl(dialect):
    out = []
    if dialect.name == "postgresql":
        seen = {}
        for t in Base.metadata.sorted_tables:
            for c in t.columns:
                if isinstance(c.type, Enum) and c.type.name not in seen:
                    seen[c.type.name] = c.type.enums
        for name, vals in seen.items():
            out.append(f"CREATE TYPE {name} AS ENUM ({', '.join(repr(v) for v in vals)});")
        out.append("")
    for t in Base.metadata.sorted_tables:
        out.append(f"-- {t.name}: {PURPOSE[t.name]}")
        out.append(str(CreateTable(t).compile(dialect=dialect)).strip() + ";")
        for ix in sorted(t.indexes, key=lambda i: i.name):
            out.append(str(CreateIndex(ix).compile(dialect=dialect)).strip() + ";")
        out.append("")
    return "\n".join(out)


header = ("-- Generated from app/models.py by docs/schema/make_schema_docs.py. Do not edit by hand.\n"
          "-- Tables are created at app startup with Base.metadata.create_all (no migrations yet).\n\n")
(OUT / "schema_sqlite.sql").write_text(header + ddl(sqlite.dialect()), encoding="utf-8")
(OUT / "schema_postgres.sql").write_text(
    header.replace("Tables are created", "PostgreSQL rendering of the same schema (production target). In SQLite tables are created")
    + ddl(postgresql.dialect()), encoding="utf-8")

# ---------------------------------------------------------------- relationships list
declared = []
for t in Base.metadata.sorted_tables:
    for c in t.columns:
        for fk in c.foreign_keys:
            tgt = fk.column.table.name
            one = c.name in single_unique(t)
            declared.append((t.name, c.name, tgt, fk.column.name, c.nullable, one))

# ---------------------------------------------------------------- SCHEMA.md
md = ["# Database schema", "",
      "Generated from `app/models.py` by `docs/schema/make_schema_docs.py`; re-run it after changing the models.",
      "The app creates these tables at startup (`Base.metadata.create_all`); there are no migrations yet, so a",
      "schema change needs `data/corpone.db` recreated or a manual `ALTER`.", "",
      "| File | What it is |", "|---|---|",
      "| [erd.mmd](erd.mmd) | ER diagram (Mermaid, rendered by GitHub) |",
      "| [erd.png](erd.png) | ER diagram as an image (exported from erd.drawio) |",
      "| [erd.drawio](erd.drawio) | ER diagram, editable in draw.io |",
      "| [schema_sqlite.sql](schema_sqlite.sql) | DDL as created today (SQLite) |",
      "| [schema_postgres.sql](schema_postgres.sql) | Same schema for PostgreSQL (production target) |", "",
      "![ER diagram](erd.png)", "",
      f"**{len(T)} tables** in {len(GROUPS)} areas. Foreign keys are declared in the models; "
      "*logical links* are matched on a shared value in code but are not foreign keys.", ""]
md += ["## Relationships", "", "| From | To | Kind | Cardinality |", "|---|---|---|---|"]
for a, ac, b, bc, nullable, one in declared:
    card = f"one `{b}` : {'0..1' if one else '0..*'} `{a}`" + (f"; `{ac}` is optional" if nullable else "")
    md.append(f"| `{a}.{ac}` | `{b}.{bc}` | foreign key | {card} |")
for a, ac, b, bc, label in LOGICAL:
    md.append(f"| `{a}.{ac}` | `{b}.{bc}` | logical ({label}) | matched by value, not enforced |")
md.append("")

enums = {}
for gname, _, _, tables in GROUPS:
    md += [f"## {gname}", ""]
    for name in tables:
        t = T[name]
        md += [f"### `{name}`", "", PURPOSE[name], "",
               "| Column | Type | Null | Default | Keys |", "|---|---|---|---|---|"]
        for c in t.columns:
            ty = coltype(c)
            if isinstance(c.type, Enum):
                enums[c.type.name] = c.type.enums
                ty = f"ENUM `{c.type.name}`"
            md.append(f"| `{c.name}` | {ty} | {'yes' if c.nullable else 'no'} | {default(c)} | {', '.join(keys(t, c))} |")
        comp = composite_uniques(t)
        if comp:
            md += ["", "Unique together: " + "; ".join("(" + ", ".join(f"`{x}`" for x in u) + ")" for u in comp)]
        md.append("")
md += ["## Enum values", "", "| Enum | Values |", "|---|---|"]
for k, v in sorted(enums.items()):
    md.append(f"| `{k}` | {', '.join(f'`{x}`' for x in v)} |")
md.append("")
(OUT / "SCHEMA.md").write_text("\n".join(md), encoding="utf-8")

# ---------------------------------------------------------------- Mermaid ERD
def mtype(c):
    ty = "enum" if isinstance(c.type, Enum) else c.type.compile(dialect=sqlite.dialect()).split("(")[0].lower()
    return ty.replace(" ", "_")


er = ["erDiagram"]
for t in Base.metadata.sorted_tables:
    er.append(f"  {t.name} {{")
    for c in t.columns:
        k = []
        if c.primary_key:
            k.append("PK")
        if c.foreign_keys:
            k.append("FK")
        if c.name in single_unique(t) and not c.primary_key:
            k.append("UK")
        er.append(f"    {mtype(c)} {c.name}{' ' + ','.join(k) if k else ''}")
    er.append("  }")
for a, ac, b, bc, nullable, one in declared:
    left = "|o" if nullable else "||"
    right = "o|" if one else "o{"
    er.append(f'  {b} {left}--{right} {a} : "{ac}"')
for a, ac, b, bc, label in LOGICAL:
    er.append(f'  {b} |o..o{{ {a} : "{label} (logical)"')
(OUT / "erd.mmd").write_text("\n".join(er) + "\n", encoding="utf-8")

# ---------------------------------------------------------------- draw.io ERD
cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
ROW, HEAD, W, GAPY = 18, 28, 270, 26
col_x = {0: 40, 1: 360, 2: 680, 3: 1000, 4: 1320}
row_id = {}
geom = {}

for gi, (gname, fill, stroke, tables) in enumerate(GROUPS):
    x = col_x[gi]
    y = 110
    top = y
    for name in tables:
        t = T[name]
        h = HEAD + ROW * len(t.columns)
        tid = f"t_{name}"
        cells.append(f'<mxCell id="{tid}" value={quoteattr(f"<b>{name}</b>")} style={quoteattr(f"swimlane;fontStyle=0;childLayout=stackLayout;horizontal=1;startSize={HEAD};horizontalStack=0;resizeParent=1;resizeParentMax=0;resizeLast=0;collapsible=1;marginBottom=0;html=1;fillColor={fill};strokeColor={stroke};rounded=1;arcSize=4;fontSize=12;")} vertex="1" parent="1">'
                     f'<mxGeometry x="{x}" y="{y}" width="{W}" height="{h}" as="geometry"/></mxCell>')
        for i, c in enumerate(t.columns):
            k = keys(t, c)
            badge = "PK" if c.primary_key else ("FK" if c.foreign_keys else ("UK" if "UNIQUE" in k else ""))
            ty = (c.type.name if isinstance(c.type, Enum) else coltype(c)).lower()
            label = (f'<span style="display:inline-block;width:22px;font-weight:bold;color:{stroke};">{badge}</span>'
                     f'{"<b>" if c.primary_key else ""}{escape(c.name)}{"</b>" if c.primary_key else ""}'
                     f'<span style="color:#6b7787;"> · {escape(ty)}{"" if c.nullable else " · not null"}</span>')
            rid = f"r_{name}_{c.name}"
            row_id[(name, c.name)] = rid
            cells.append(f'<mxCell id="{rid}" value={quoteattr(label)} style={quoteattr("text;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;spacingLeft=6;spacingRight=4;overflow=hidden;rotatable=0;points=[[0,0.5],[1,0.5]];portConstraint=eastwest;html=1;fontSize=10.5;")} vertex="1" parent="{tid}">'
                         f'<mxGeometry y="{HEAD + i * ROW}" width="{W}" height="{ROW}" as="geometry"/></mxCell>')
        geom[name] = (x, y, h)
        y += h + GAPY
    cells.append(f'<mxCell id="g{gi}" value={quoteattr(gname)} style={quoteattr(f"rounded=1;arcSize=2;whiteSpace=wrap;html=1;fillColor=none;strokeColor={stroke};dashed=1;verticalAlign=top;align=left;spacingLeft=10;spacingTop=4;fontStyle=1;fontSize=13;fontColor={stroke};")} vertex="1" parent="1">'
                 f'<mxGeometry x="{x - 14}" y="{top - 34}" width="{W + 28}" height="{y - top + 24}" as="geometry"/></mxCell>')
# put the frames behind the tables
frames = [c for c in cells if c.startswith('<mxCell id="g')]
cells = cells[:2] + frames + [c for c in cells[2:] if c not in frames]

ER = "edgeStyle=entityRelationEdgeStyle;html=1;fontSize=10;strokeColor=#5b6675;labelBackgroundColor=#ffffff;"
n = 0
for a, ac, b, bc, nullable, one in declared:
    n += 1
    start = "ERzeroToOne" if nullable else "ERmandOne"
    end = "ERzeroToOne" if one else "ERzeroToMany"
    cells.append(f'<mxCell id="fk{n}" value="" style={quoteattr(ER + f"startArrow={start};endArrow={end};startFill=0;endFill=0;")} edge="1" parent="1" '
                 f'source="{row_id[(b, bc)]}" target="{row_id[(a, ac)]}"><mxGeometry relative="1" as="geometry"/></mxCell>')
for a, ac, b, bc, label in LOGICAL:
    n += 1
    cells.append(f'<mxCell id="lk{n}" value="" style={quoteattr(ER + "dashed=1;strokeColor=#9aa5b4;startArrow=ERzeroToOne;endArrow=ERzeroToMany;startFill=0;endFill=0;")} edge="1" parent="1" '
                 f'source="{row_id[(b, bc)]}" target="{row_id[(a, ac)]}"><mxGeometry relative="1" as="geometry"/></mxCell>')

maxy = max(y + h for _, y, h in geom.values())
title = ('<span style="font-size:22px;font-weight:bold;">CorpOne Corporate Portal · ER diagram</span><br>'
         f'<span style="font-size:12px;color:#6b7787;">{len(T)} tables from app/models.py · PK primary key · FK foreign key · '
         'UK unique · solid line = foreign key · dashed line = logical link (matched by value, not enforced) · '
         'crow\'s foot = many, bar = one, circle = optional</span>')
cells.insert(2, f'<mxCell id="title" value={quoteattr(title)} style="text;html=1;align=left;verticalAlign=top;whiteSpace=wrap;" vertex="1" parent="1">'
                f'<mxGeometry x="26" y="10" width="1500" height="50" as="geometry"/></mxCell>')
xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<mxfile host="Electron" agent="corpone-docs-generator" version="24.7.0">'
       f'<diagram id="erd" name="ER diagram"><mxGraphModel dx="1600" dy="900" grid="1" gridSize="10" guides="1" tooltips="1" '
       f'connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1640" pageHeight="{maxy + 60}" math="0" shadow="0">'
       "<root>" + "".join(cells) + "</root></mxGraphModel></diagram></mxfile>\n")
(OUT / "erd.drawio").write_text(xml, encoding="utf-8")
print(f"wrote schema docs for {len(T)} tables, {len(declared)} foreign keys, {len(LOGICAL)} logical links into {OUT}")
