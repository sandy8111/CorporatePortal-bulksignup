# CorpOne Corporate Bulk-Line Portal

Web portal for corporate customers to sign up bulk mobile lines (eSIM or physical SIM), plus a
back office for SIM/number inventory and blacklist management.

## Run locally

```bash
cd corpone-portal
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env        # set CORPONE_STORAGE_BACKEND=local if you have no AWS credentials
.venv/bin/uvicorn app.main:app --reload
```

- Sign-up portal: http://localhost:8000/ (sign in with an email one-time code)
- Back office: http://localhost:8000/admin (enter `CORPONE_ADMIN_TOKEN`)
- Portal API docs: http://localhost:8000/docs
- Partner & AI gateway docs: http://localhost:8000/gateway/docs (spec at `/gateway/openapi.json`)
- Tests: `.venv/bin/python -m pytest`

Quick demo: in the back office, allocate SIM category `ACME-SIM-001` to UEN `197401143C`, then
upload `sample_data/sims.csv` and `sample_data/numbers.csv` (both use `ACME-SIM-001`). Open the
portal and sign in with any email; with the default `console` email backend, the code is in the
newest `.eml` file in `data/outbox/`. Then sign up with UEN `197401143C`, NRIC `S1234567D` and
merchant code `CORP-DIRECT-01`, and choose `ACME-SIM-001` in the plan & lines step.

> Upgrading from an earlier version: tables are created on startup, so delete `data/corpone.db`
> once to pick up schema changes (the latest: SIM category moved from the company step to the
> plan & lines step, and the `category_allocations` table, whose `uen` is now optional). Production needs Alembic migrations.

## Account officer login (email OTP)

1. The AO enters a work email. The portal emails a 6-digit code.
2. The AO enters the code and gets a session: an HttpOnly, SameSite=Strict cookie, 8 hours by
   default. The verify response also returns the token for API clients, used as
   `Authorization: Bearer`.

Security rules:
- Codes are stored as an HMAC with `CORPONE_OTP_PEPPER`, never in plain text.
- A code expires after 5 minutes and works once. Only the newest code is valid.
- After 5 wrong attempts the code is burned.
- Limits: one resend per 30 seconds, 5 codes per email per 15 minutes, and 20 requests per IP
  per 15 minutes.

**Test environments without email:** set both `CORPONE_DEV_FIXED_OTP=123456` and
`CORPONE_DEV_FIXED_OTP_EMAIL=admin@corp1.com.sg`. That one account then always signs in with
`123456`. Test mode is off unless both are set. Every other email still gets a random code by
email. The login page shows a "test environment" banner naming the account (never the code), and
the server logs a warning at startup. Expiry, attempt limits and rate limits still apply. Never set
this in production.

Every sign-up belongs to the email that created it. Another AO gets a 404, and order status is
visible only to that account's AO. The AO email on the order is always the verified login email,
so eSIM QR codes can only go to an inbox the AO has proved they control.

## Sign-up flow

| Step | What happens | Rules |
|---|---|---|
| 1. Company | Enter UEN, name, address; upload contract. The details stay editable after validation ("Save changes") | UEN format and check letter (ACRA business, local company, other entity); company must not be blacklisted; contract must be a PDF ≤ 10 MB, stored in S3 with SSE. Changing the UEN re-runs these checks, is refused while the cart has lines, and clears the contract (upload it again) and any SIM category not allocated to the new UEN. Changing only the name or address keeps everything |
| 2. Account officer | Name, NRIC/FIN/passport, email, mobile; pick the delegated merchant code from a drop-down of active codes (managed in the back office) | NRIC/FIN check letter; AO must not be blacklisted; merchant code must exist and be active |
| 3. Plan & lines | Choose the SIM category; pick $8 Ray / $20 Flash / $40 Glare; optionally upload SIMs; pick SIM type and quantity | The category must be allocated to this UEN (e.g. `ACME-SIM-001`) or open to all companies, and is required before SIM upload, inventory, cart and submit. It can't be changed while the cart has lines. Allocates already-paired SIM+number lines from that category, then reserves them in the cart |
| 4. Review | Cart summary per plan, line list, monthly total; confirm and submit | Blacklist checked again at submit |
| 5. Status | Live provisioning status per line | |

**On submit** (one DB transaction): Company profile is created or reused by UEN, then the Account
Officer, then the Corporate Account. Each Line is created and its SIM and number are marked
`ASSIGNED`. **Then, in the background:** a billing account is created, each line is activated in
the network and subscribed in billing, and the eSIM QR codes are emailed to the AO. The email holds
one PNG per line (SGP.22 `LPA:1$<SM-DP+>$<MatchingID>`), up to 50 per email. Failed lines are
marked `FAILED` with the error. `POST /api/admin/accounts/{acct}/retry-provisioning` retries only
the incomplete steps, using idempotency keys.

## Gateways

### Inbound: partner and AI gateway (`/gateway/v1`)

External systems (dealer CRMs, ops tools) and AI agents call the portal through a separate API with
its own OpenAPI spec:

- **API keys:** issued in the back office. The key (`cpk_<prefix>_<secret>`) is shown once and
  stored hashed. Send it as `Authorization: Bearer` or `X-API-Key`. Keys can be revoked.
- **Scopes:** `plans:read`, `validation:read`, `inventory:read`, `inventory:write`,
  `accounts:read`, `provisioning:write`.
- **Rate limits:** per key per minute, with `X-RateLimit-*` headers. Over the limit returns 429
  with `Retry-After`.
- **Audit:** every request (key, path, status, latency, IP) is logged and shown in the back office.

### MCP server for AI agents (`mcp_server/`)

Exposes these tools: `list_plans`, `validate_uen`, `check_blacklist`, `inventory_summary`,
`get_account_status`, `retry_provisioning`. It calls the gateway with its own API key, so AI tool
calls get the same scopes, rate limits and audit trail as any other client. Give the agent's key
only the scopes it needs.

```bash
export CORPONE_GATEWAY_URL=http://localhost:8000/gateway CORPONE_GATEWAY_API_KEY=cpk_...
.venv/bin/python -m mcp_server.server          # stdio: Claude Desktop / Claude Code
.venv/bin/python -m mcp_server.server --http   # streamable HTTP on :8000/mcp (e.g. Bedrock AgentCore Runtime)
```

You can also register `/gateway/openapi.json` directly as an OpenAPI target in Amazon Bedrock
AgentCore Gateway.

### Outbound gateway (`app/gateway/outbound.py`)

Every call the portal makes to the **network API**, **billing API** or **AI model** goes through
one layer. Each upstream has its own policy:

| Control | Behaviour |
|---|---|
| Rate limit | Token bucket per upstream; waits for capacity instead of failing |
| Retries | Exponential backoff with jitter, only on retryable errors (5xx, 429, connection/timeouts). Safe because telco calls send `Idempotency-Key`. AI calls aren't retried here because the Anthropic SDK already retries. |
| Circuit breaker | Opens after 5 consecutive failures and fails fast for 30 seconds, then tries again. Can be reset from the back office. |
| Call log | Every attempt (upstream, operation, outcome, latency, error) is logged and shown in the back office |

### AI contract check (Claude)

Off by default. With `CORPONE_AI_PROVIDER=anthropic` (Claude API, `claude-opus-5-5`) or `bedrock`
(Amazon Bedrock, `anthropic.claude-opus-5-5`), Claude reads each uploaded contract PDF in the
background. It returns structured JSON: company name, UEN, whether it looks signed, and issues.
The portal compares the results with what the AO entered and shows them at the review step.

- **Advisory only:** it never blocks a sign-up, and if the model is unavailable or declines, the
  page says the contract will be checked manually.
- **Refusal fallback** is on. The Claude API uses server-side `fallbacks: "default"`. Bedrock uses
  the SDK's refusal-fallback middleware with `anthropic.claude-opus-4-8`.
- **Untrusted input:** the contract text is treated as data, the model has no tools, and its
  output is only displayed.

## Inventory

- **SIM upload** CSV: `iccid,sim_type,category[,smdp_address,matching_id]`. ICCID must be numeric,
  18–20 digits. `sim_type` is `ESIM` or `PHYSICAL`.
- **Number upload** CSV: `msisdn,category`. Must be 8 digits starting with 8 or 9 (`+65` prefix is
  stripped).
- A header row is optional. Without one, the file is one value per line and the form's default
  category or SIM type is used.
- Validation is per row. Valid rows are loaded, and invalid or duplicate rows come back with a
  reason.
- **Auto-pairing** runs after every upload. Unpaired SIMs are paired with unpaired numbers of the
  **same category**, oldest first. Status lifecycle: `AVAILABLE → PAIRED → RESERVED (cart) →
  ASSIGNED (line)`.
- **Who can use a SIM category** (back office card "SIM categories: who can use them",
  `PUT /api/admin/categories`):
  - *Allocated to a company:* give a UEN, e.g. `ACME-SIM-001` → `197401143C`. Only that company's
    AOs can choose it. A company can have several.
  - *For all companies:* leave the UEN blank. A category that has SIMs/numbers but was never
    allocated is also open to all companies.
  - *Deactivated:* hidden from every company. Draft sign-ups using it can't add lines or submit
    until they choose another category.
  AOs choose from their own categories first, then the shared ones
  (`GET /api/signups/{id}/categories`, `PUT /api/signups/{id}/category`).
- SIMs that an AO uploads in the wizard are locked to the sign-up's chosen category.
- The upload result lists every SIM ↔ number pair it made. A SIM or number that was already
  uploaded is reported with its current pairing, but only when it is in the same category as the
  row. Otherwise the result just says "already uploaded under another category".
- **Pairing view.** The back office card "SIM ↔ number pairing"
  (`GET /api/admin/inventory/pairings?category=…`) lists each SIM with its MSISDN and the status
  of both sides. You can filter by status and see numbers still waiting for a SIM
  (`status=NUMBERS_WAITING`). A row is highlighted if the SIM and number statuses ever differ. In
  the portal, the AO's list (`GET /api/signups/{id}/sims`) shows only free lines (not paired yet,
  or paired and ready) and that sign-up's own cart lines. It never shows lines reserved by other
  sign-ups or live lines. Page size is 1–500.

## Blacklist

Entries hold a subject (`COMPANY` with a UEN, or `ACCOUNT_OFFICER` with NRIC/FIN/passport), a
reason, a `start_date`, and an optional `end_date` (blank means indefinite). An entry blocks
sign-up only while it is in force (`start_date ≤ today ≤ end_date`).

## Watching logs and API calls while testing

Start the server in its own terminal and keep a copy of the log:

```bash
.venv/bin/uvicorn app.main:app --reload --no-access-log 2>&1 | tee data/server.log
```

Each API call prints one line (page files under `/static` and `/healthz` are skipped):

```
INFO corpone.api: GET /api/signups -> 200 3ms by=admin@corp1.com.sg id=9e8a8a42
WARNING corpone.api: GET /api/admin/inventory/pairings?category=ACME-SIM-001 -> 401 1ms by=- id=87d9954b
```

- `by` is the signed-in AO's email, `admin` for the back office, `api-client:<id>` for a gateway
  key, or `-` when the caller isn't signed in.
- 4xx lines are logged as WARNING and 5xx as ERROR, with the traceback.
- `id` is also returned in the `X-Request-ID` response header. Find a call in the browser's
  DevTools → Network tab, copy that header, and run `grep <id> data/server.log` to find it.
  A client can send its own `X-Request-ID` to set the ID.
- Request and response bodies are never logged, because they hold OTP codes, NRIC numbers and
  contracts. Use DevTools → Network → Payload / Response to see them.
- Useful filters: `tail -f data/server.log | grep corpone.api`, and
  `grep -E "WARNING|ERROR" data/server.log` for failed calls only.
- `--no-access-log` turns off uvicorn's own per-request line, which would duplicate this one.
  Set `CORPONE_REQUEST_LOG=false` to turn this log off.
- Sign-in codes sent with the `console` email backend are in `data/outbox/*.eml`. Gateway and
  upstream calls are also listed under back office → Gateway monitoring.

## Configuration (`.env`, prefix `CORPONE_`)

| Setting | Options |
|---|---|
| `DATABASE_URL` | SQLite by default; use PostgreSQL/RDS in production |
| `STORAGE_BACKEND` | `s3` (with `S3_BUCKET` and `AWS_REGION`) or `local` |
| `EMAIL_BACKEND` | `console` (writes `.eml` to `data/outbox`), `smtp`, or `ses` |
| `TELCO_API_MODE` | `mock` or `http` (with `NETWORK_API_BASE_URL`, `BILLING_API_BASE_URL`, `TELCO_API_KEY`) |
| `ADMIN_TOKEN` | Required in the `X-Admin-Token` header for `/api/admin/*` |
| `OTP_PEPPER`, `OTP_*`, `SESSION_TTL_HOURS`, `COOKIE_SECURE` | AO login. Set a long random pepper and `COOKIE_SECURE=true` in production. |
| `GATEWAY_DEFAULT_RATE_PER_MIN` | Default per-key limit for `/gateway/v1` |
| `OUTBOUND_*` | Retry, backoff, circuit-breaker and rate policy for upstreams |
| `AI_PROVIDER`, `AI_MODEL`, `AI_RATE_PER_SEC` | AI contract check (`disabled` / `anthropic` / `bedrock`) |

## Before production

- **Telco API contract:** `app/services/telco_api.py` uses placeholder paths and payloads. Map them
  to the real network (HLR/HSS, SM-DP+) and billing (BSS) APIs.
- **UEN existence:** the validator checks UEN format and check letter only. To confirm the entity
  is registered and live, add a lookup against ACRA (BizFile+ API or the data.gov.sg entity
  dataset).
- **Back-office authentication:** the admin side still uses one shared token. Put staff SSO (for
  example Cognito with your identity provider) in front of it, and record which staff member
  changed what.
- **Multiple instances:** rate limiters and circuit-breaker state live in each process. When
  running several instances, move them to Redis/ElastiCache, or put Amazon API Gateway usage
  plans and AWS WAF in front of `/gateway` and the OTP endpoints.
- **Plans:** data allowances in `app/seed.py` are placeholders.
- **Schema migrations:** tables are created on startup; add Alembic for production schema changes.
- **Provisioning at scale:** it runs in a FastAPI background task. For large orders, move it to a
  queue such as SQS with a worker.
- **eSIM matching ID:** if the SIM file has no `matching_id`, the ICCID is used. Supply the real
  activation codes from the SM-DP+ provider in the SIM file.
