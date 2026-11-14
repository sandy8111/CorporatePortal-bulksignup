"""Build the deployment notebooks.

  python notebooks/make_notebook.py notebooks/deploy_to_production.ipynb
  python notebooks/make_notebook.py notebooks/deploy_test_us-east-1.ipynb --test
"""
import sys

import nbformat as nbf

OUT = sys.argv[1]
TEST = "--test" in sys.argv[2:]
cells = []
md = lambda s: cells.append(nbf.v4.new_markdown_cell(s.strip()))
code = lambda s: cells.append(nbf.v4.new_code_cell(s.strip()))

md("""
# Deploy corpone-agentcore to production

This notebook takes the CorpOne assistant from a tested build to a production deployment on Amazon Bedrock
AgentCore, with a gate at every step. It uses the same code as the command line (`infra/deploy.py`) and the
helpers in `infra/corpone_infra/prod.py`.

| Phase | What happens | Changes AWS? |
|---|---|---|
| 0 Settings | Region, portal URL, model, safety switch | no |
| 1 Preflight | AWS identity, AgentCore and model in region, portal reachable with the API key, Docker, all test suites | no |
| 2 Image | Build the assistant image (linux/arm64) and push it to ECR | **yes** (ECR) |
| 3 Plan | Dry run: every request that will be sent | no |
| 4 Deploy | Create the stack with Policy in `LOG_ONLY` | **yes** |
| 5 Tracing | CloudWatch Transaction Search (once per account and region) | **yes** |
| 6 Smoke test | Test users per role, one assistant call each | **yes** (Cognito users) |
| 7 Policy | Talk to the Gateway directly as each role | no |
| 8 Evaluation gate | 12 scenarios, local scoring + AgentCore batch evaluation | evaluation run |
| 9 Enforce | Switch Policy to `ENFORCE`, verify officers are blocked | **yes** |
| 10 Observability | Spans arriving, online evaluation active | no |
| 11 Go live | Checklist, clean-up of test users, rollback commands | optional |

**Safety:** nothing changes in AWS while `CONFIRM_DEPLOY = False`; those cells print what they would do and skip.
The portal API key is typed in with `getpass`, sent only to AgentCore Identity, and never saved.

**Before you start:** the portal must already run in AWS behind HTTPS, and you need a portal API key with scopes
`plans:read validation:read inventory:read accounts:read provisioning:write` (portal back office →
*API gateway clients*). Estimated running cost: about $30–35 a month plus model tokens (see `docs/OPERATIONS.md`).
""")

md("## 0 · Settings")
code("""
import json, os, sys, uuid
from pathlib import Path

ROOT = Path.cwd().resolve().parent if Path.cwd().name == "notebooks" else Path.cwd().resolve()
sys.path[:0] = [str(ROOT / "infra"), str(ROOT / "evals")]

# Load infra/.env if present (it must not contain PORTAL_API_KEY)
env_file = ROOT / "infra" / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.split("#", 1)[0].strip())

# Explicit production settings: these override anything already in the shell environment.
REGION = "ap-southeast-1"          # Singapore: NRIC / PDPA data stays in-country
if os.environ.get("AWS_REGION") not in (None, REGION):
    print(f"⚠️  shell AWS_REGION={os.environ['AWS_REGION']} is overridden with {REGION}")
os.environ["AWS_REGION"] = os.environ["AWS_DEFAULT_REGION"] = REGION
os.environ["PROJECT"] = "corpone-agentcore"
os.environ["POLICY_MODE"] = "LOG_ONLY"   # always start in LOG_ONLY; phase 9 switches to ENFORCE
os.environ.setdefault("EVAL_SAMPLING_PERCENT", "10")
os.environ.setdefault("MEMORY_EXPIRY_DAYS", "7")
# Required for production (set here or in infra/.env):
# os.environ["PORTAL_GATEWAY_URL"] = "https://portal.corpone.sg/gateway"
# os.environ["MODEL_ID"] = "<Bedrock model or inference profile id available in the region>"

CONFIRM_DEPLOY = False      # set True only when you intend to create or change AWS resources
TEST_USERS = {"officer": "assistant-smoke-officer@corpone.sg", "ops": "assistant-smoke-ops@corpone.sg"}

from corpone_infra.config import InfraConfig
cfg = InfraConfig()
print(json.dumps({k: getattr(cfg, k) for k in ("project", "region", "portal_gateway_url", "model_id",
                                              "agent_image_uri", "policy_mode")}, indent=2))
print("CONFIRM_DEPLOY =", CONFIRM_DEPLOY)
""")
code("""
import getpass
if not os.environ.get("PORTAL_API_KEY"):
    try:
        key = getpass.getpass("Portal API key (cpk_..., input hidden; leave empty to skip): ").strip()
    except Exception:          # non-interactive run (e.g. CI rehearsal): no prompt
        key = ""
    if key:
        os.environ["PORTAL_API_KEY"] = key
print("PORTAL_API_KEY set:", os.environ.get("PORTAL_API_KEY", "").startswith("cpk_"))
""")

md("""
## 1 · Preflight checks (read-only)

Every check must be ✅ before deploying. Nothing here creates or changes anything.
""")
code("""
import boto3, httpx
from corpone_infra import prod

session = boto3.Session(region_name=cfg.region)
results = {}

def check(name, ok, detail=""):
    results[name] = bool(ok)
    print(("✅" if ok else "❌"), name, "-", detail)

try:
    ident = prod.aws_identity(session.client("sts"))
    cfg.account_id = ident["account"]
    check("AWS credentials", True, f"account {ident['account']} as {ident['arn'].split(':')[-1]}")
except Exception as exc:
    check("AWS credentials", False, type(exc).__name__)

ok, detail = prod.agentcore_available(session.client("bedrock-agentcore-control"))
check(f"AgentCore in {cfg.region}", ok, detail)

if cfg.model_id:
    ok, detail = prod.model_available(session.client("bedrock"), cfg.model_id)
    check(f"Model {cfg.model_id}", ok, detail)
else:
    check("MODEL_ID set", False, "set MODEL_ID in phase 0")

for p in cfg.problems(for_apply=False):
    check("settings", False, p)

if cfg.portal_gateway_url.startswith("https://") and os.environ.get("PORTAL_API_KEY"):
    probes = prod.portal_checks(httpx.Client(), cfg.portal_gateway_url, os.environ["PORTAL_API_KEY"])
    for tool, r in probes.items():
        check(f"portal {tool}", r["ok"], f"HTTP {r['status']}")
else:
    check("portal reachable with API key", False, "needs PORTAL_GATEWAY_URL (https) and PORTAL_API_KEY")

check("Docker", prod.docker_available(), "needed to build the image (phase 2)")

for name, ok, detail in prod.service_access(session):
    check(name, ok, detail)
""")
code("""
ok, tail = prod.run_test_suites()     # portal, agent, infra, evals - no AWS calls
check("all test suites", ok, tail.strip().splitlines()[-1] if tail.strip() else "")
print(tail)
PREFLIGHT_OK = all(results.values())
print("\\nPREFLIGHT_OK =", PREFLIGHT_OK)
""")

md("""
## 2 · Build and push the assistant image

Builds `agent/` for linux/arm64 (required by AgentCore Runtime), creates the ECR repository with scan-on-push
if needed, and pushes. Copy the printed `AGENT_IMAGE_URI` into `infra/.env` for next time.
""")
code("""
import subprocess
if not CONFIRM_DEPLOY:
    print("CONFIRM_DEPLOY is False: would run agent/build_and_push.sh in", cfg.region)
elif cfg.agent_image_uri:
    print("Using existing image:", cfg.agent_image_uri)
else:
    r = subprocess.run([str(ROOT / "agent" / "build_and_push.sh")], capture_output=True, text=True,
                       env={**os.environ, "AWS_REGION": cfg.region})
    print(r.stdout[-1500:], r.stderr[-1500:])
    uri = next((l.split("=", 1)[1] for l in r.stdout.splitlines() if l.startswith("AGENT_IMAGE_URI=")), "")
    if r.returncode or not uri:
        raise RuntimeError("image build/push failed")
    os.environ["AGENT_IMAGE_URI"] = cfg.agent_image_uri = uri
    print("AGENT_IMAGE_URI =", uri)
""")

md("""
## 3 · Plan (dry run)

Every request the deployment will send, in order, with the API key masked. Review it before phase 4.
""")
code("""
import deploy
deploy.dry_run(cfg)
""")

md("""
## 4 · Deploy (Policy in LOG_ONLY)

Creates the 21 resources: Cognito, IAM roles, Identity credential provider, Policy engine, Gateway and target,
4 Cedar policies, Memory, Runtime, online evaluation. Resumable: if a step fails, fix the cause and re-run the
cell; finished steps are skipped (state in `infra/build/state-<project>-<region>.json`, no secrets).
""")
code("""
state = deploy.load_state(cfg)
if not CONFIRM_DEPLOY:
    print("CONFIRM_DEPLOY is False: nothing created.", f"{len(state['done'])} step(s) already in state.")
else:
    assert PREFLIGHT_OK, "fix the preflight checks first"
    problems = cfg.problems(for_apply=True)
    assert not problems, problems
    deploy.apply(cfg, state)
    state = deploy.load_state(cfg)

OUT = state["outputs"]
DEPLOYED = "runtime_arn" in OUT and "gateway_url" in OUT
print("DEPLOYED =", DEPLOYED)
print(json.dumps({k: OUT.get(k) for k in ("gateway_url", "runtime_arn", "memory_id", "user_pool_id",
                                          "app_client_id", "online_eval_id")}, indent=2))
""")

md("""
## 5 · Tracing destination (once per account and region)

AgentCore Observability, Evaluations and CloudWatch Omni read spans from the `aws/spans` log group, which needs
CloudWatch Transaction Search. If AWS asks for a CloudWatch Logs resource policy, follow the Transaction Search
setup page and re-run.
""")
code("""
from botocore.exceptions import ClientError
xray = session.client("xray")
try:
    current = xray.get_trace_segment_destination()
except ClientError as exc:
    current = {"Destination": None}
    print("❌ cannot read the trace destination:", exc.response["Error"]["Code"], "-",
          exc.response["Error"]["Message"][:200])
print("current destination:", current.get("Destination"), current.get("Status"))
if current.get("Destination") is None:
    print("Fix the permission (or SCP) for xray:GetTraceSegmentDestination / UpdateTraceSegmentDestination first.")
elif current.get("Destination") != "CloudWatchLogs":
    if CONFIRM_DEPLOY:
        xray.update_trace_segment_destination(Destination="CloudWatchLogs")
        print("Transaction Search enabled")
    else:
        print("CONFIRM_DEPLOY is False: would enable Transaction Search (destination CloudWatchLogs)")
""")

md("""
## 6 · Smoke test with one test user per role

Creates (or resets) two admin-created test users with random passwords kept only in this notebook's memory,
signs them in server-side, and asks the assistant one question each.
""")
code("""
TOKENS = {}
if not DEPLOYED:
    print("Not deployed yet: skipped.")
elif not CONFIRM_DEPLOY:
    print("CONFIRM_DEPLOY is False: would create test users", TEST_USERS)
else:
    cognito = session.client("cognito-idp")
    for role, email in TEST_USERS.items():
        pw = prod.strong_password()
        prod.ensure_user(cognito, OUT["user_pool_id"], email, role, pw)
        TOKENS[role] = prod.access_token(cognito, OUT["user_pool_id"], OUT["app_client_id"], email, pw)
    print("tokens for:", sorted(TOKENS))
""")
code("""
SMOKE = {"officer": "Which plans do you offer?", "ops": "Give me a one-line inventory summary per SIM category."}
for role, prompt in SMOKE.items():
    if role not in TOKENS:
        print(role, ": skipped (no token)"); continue
    r = prod.invoke_runtime(cfg.region, OUT["runtime_arn"], TOKENS[role], f"smoke-{role}-{uuid.uuid4().hex}", prompt)
    meta = r.get("metadata", {})
    print(f"[{role}] status={r.get('status')} role={meta.get('role')} tools_used={meta.get('tools_used')}")
    print("   ", (r.get("response") or r.get("error", ""))[:300])
""")

md("""
## 6b · Try the assistant in your browser

The deployment wrote the portal's assistant settings (no secrets) to `infra/build/portal-assistant-<project>-<region>.env`.
Restart the portal with them, then open the assistant page and sign in through Cognito's hosted login page.
Optionally invite yourself: you get a one-time temporary password and choose your own at first sign-in.
""")
code("""
BROWSER_USER = ""        # e.g. "you@corpone.sg"; empty = don't invite anyone
BROWSER_ROLE = "ops"     # "ops" or "officer"
if not DEPLOYED:
    print("Not deployed yet: skipped.")
else:
    env_path = deploy.write_portal_env(cfg, OUT)
    print("Portal settings:", env_path)
    print(env_path.read_text())
    print("Restart the portal with them, e.g.:\\n"
          f"  set -a; . {env_path}; set +a; (cd portal && ../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8501)")
    print("\\nAssistant page:", cfg.assistant_url)
    print("Hosted login:  ", cfg.login_url)
    if BROWSER_USER and CONFIRM_DEPLOY:
        temp = prod.invite_user(session.client("cognito-idp"), OUT["user_pool_id"], BROWSER_USER, BROWSER_ROLE)
        print(f"\\nInvited {BROWSER_USER} ({BROWSER_ROLE}). One-time temporary password (shown once, not saved):\\n  {temp}")
""")

md("""
## 7 · Policy check, straight to the Gateway

Bypasses the agent and calls the Gateway as each role. In `LOG_ONLY` every call succeeds and decisions are only
logged; look at the Gateway's policy decision logs in CloudWatch and confirm officer calls to ops tools are
logged as *deny*. Phase 9 repeats this check with `enforced=True`.
""")
code("""
if TOKENS:
    report = prod.verify_policy(lambda t: prod.gateway_tools(OUT["gateway_url"], t),
                                lambda t, tool, args: prod.gateway_call(OUT["gateway_url"], t, tool, args),
                                TOKENS["officer"], TOKENS["ops"], enforced=(cfg.policy_mode == "ENFORCE"))
    print(json.dumps(report, indent=2))
else:
    print("No tokens: run phase 6 first.")
""")

md("""
## 8 · Evaluation gate

Runs the 12 scenarios in `evals/dataset.jsonl` (officer and ops, including a prompt-injection attempt and NRIC
handling) against the Runtime. Local scoring must reach the 90% gate; `--batch` also starts an AgentCore batch
evaluation with each scenario's assertions and expected tools as ground truth (results in CloudWatch).
""")
code("""
import run_offline_eval
GATE_OK = False
if TOKENS:
    os.environ["OFFICER_TOKEN"], os.environ["OPS_TOKEN"] = TOKENS["officer"], TOKENS["ops"]
    try:
        run_offline_eval.main(["--runtime-arn", OUT["runtime_arn"], "--region", cfg.region, "--batch", "--min-pass", "0.9"])
    except SystemExit as e:
        GATE_OK = (e.code == 0)
    print("GATE_OK =", GATE_OK)
else:
    print("No tokens: run phase 6 first.")
""")

md("""
## 9 · Switch Policy to ENFORCE and verify

Only after phases 6–8 pass. Then officers must no longer see or call the ops-only tools.
""")
code("""
if not (DEPLOYED and GATE_OK):
    print("Skipped: needs a deployment and a passing evaluation gate.")
elif not CONFIRM_DEPLOY:
    print("CONFIRM_DEPLOY is False: would switch the Gateway policy mode to ENFORCE")
else:
    deploy.apply(cfg, deploy.load_state(cfg), only_policy_mode="ENFORCE")
    cfg.policy_mode = "ENFORCE"
    import time; time.sleep(15)
    report = prod.verify_policy(lambda t: prod.gateway_tools(OUT["gateway_url"], t),
                                lambda t, tool, args: prod.gateway_call(OUT["gateway_url"], t, tool, args),
                                TOKENS["officer"], TOKENS["ops"], enforced=True)
    print(json.dumps(report["checks"], indent=2))
    assert report["passed"], "Policy is not blocking officers: roll back with --policy-mode LOG_ONLY and investigate"
""")

md("## 10 · Observability and online evaluation")
code("""
if DEPLOYED:
    service = f"{cfg.names()['runtime']}.DEFAULT"
    spans = prod.recent_span_count(session.client("logs"), service, minutes=60)
    print(f"spans from {service} in the last hour: {spans}", "✅" if spans else "❌ (check phase 5 and the runtime logs)")
    print("online evaluation:", prod.online_eval_status(session.client("bedrock-agentcore-control"), OUT["online_eval_id"]))
    print(f"Runtime logs: /aws/bedrock-agentcore/runtimes/{OUT['runtime_id']}-DEFAULT")
    print("CloudWatch console: GenAI Observability > Bedrock AgentCore; or CloudWatch Omni with SSO")
else:
    print("Not deployed yet: skipped.")
""")

md("""
## 11 · Go live

- [ ] Preflight all ✅, image scanned in ECR with no critical findings
- [ ] Smoke test: officer used `listPlans`, ops used `getInventorySummary`
- [ ] Evaluation gate ≥ 90%, batch evaluation results reviewed
- [ ] Policy in `ENFORCE`; phase 9 checks passed
- [ ] Spans arriving; online evaluation `ACTIVE`; CloudWatch alarms set (policy denies, error rate, retryProvisioning
      calls, evaluation scores; see `docs/OPERATIONS.md`)
- [ ] Real users: Cognito hosted UI or federation, MFA on (`set_user_pool_mfa_config`); test users deleted (next cell)
- [ ] Portal: `CORPONE_TRUSTED_PROXIES` set for the load balancer, `CORPONE_COOKIE_SECURE=true`, OpenTelemetry on

**Rollback**

| Situation | Command |
|---|---|
| Stop every tool call now | `python infra/deploy.py --apply --kill-switch on` (off to restore) |
| Policy too strict | `python infra/deploy.py --apply --policy-mode LOG_ONLY` |
| Bad assistant image | push the previous tag and update the Runtime's container URI (or re-deploy with that `AGENT_IMAGE_URI`) |
| Remove everything | `python infra/destroy.py` (list) then `--yes` |
""")
code("""
DELETE_TEST_USERS = False     # set True after go-live checks
if DEPLOYED and DELETE_TEST_USERS and CONFIRM_DEPLOY:
    cognito = session.client("cognito-idp")
    for email in TEST_USERS.values():
        prod.delete_user(cognito, OUT["user_pool_id"], email)
    print("test users deleted")
else:
    print("test users kept (DELETE_TEST_USERS / CONFIRM_DEPLOY are False)")
""")

if TEST:
    # ---------------------------------------------------------------- test variant: us-east-1, test project, tear-down
    cells[0].source = ("> **TEST DEPLOYMENT** · us-east-1 · project `corpone-agentcore-test` · test data only. "
                       "Production stays in ap-southeast-1 with `deploy_to_production.ipynb`.\n\n" + cells[0].source)
    settings = cells[2]
    swaps = [
        ('REGION = "ap-southeast-1"          # Singapore: NRIC / PDPA data stays in-country',
         'REGION = "us-east-1"               # TEST ONLY: AgentCore is blocked in ap-southeast-1 in the workshop account'),
        ('os.environ["PROJECT"] = "corpone-agentcore"', 'os.environ["PROJECT"] = "corpone-agentcore-test"'),
        ('# os.environ["PORTAL_GATEWAY_URL"] = "https://portal.corpone.sg/gateway"',
         'IDE_DOMAIN = "<your-ide-domain>.cloudfront.net"     # <-- the address in your IDE browser tab\n'
         'os.environ["PORTAL_GATEWAY_URL"] = f"https://{IDE_DOMAIN}/ports/8501/gateway"'),
        ('# os.environ["MODEL_ID"] = "<Bedrock model or inference profile id available in the region>"',
         'os.environ["MODEL_ID"] = "us.anthropic.claude-sonnet-5-5"'),
        ('TEST_USERS = {"officer": "assistant-smoke-officer@corpone.sg", "ops": "assistant-smoke-ops@corpone.sg"}',
         'TEST_USERS = {"officer": "assistant-test-officer@corpone.sg", "ops": "assistant-test-ops@corpone.sg"}\n'
         'assert "<your-ide-domain>" not in os.environ["PORTAL_GATEWAY_URL"], "set IDE_DOMAIN to your IDE address first"'),
    ]
    for a, b in swaps:
        assert a in settings.source, a
        settings.source = settings.source.replace(a, b)
    cells.append(nbf.v4.new_markdown_cell("## 12 · Tear down the test stack\n\nDeletes everything this test created "
                                          "(state file `infra/build/state-corpone-agentcore-test-us-east-1.json`). "
                                          "The ECR repository and log groups are kept."))
    cells.append(nbf.v4.new_code_cell('''DESTROY_TEST_STACK = False     # set True when you are done testing
import destroy
if DESTROY_TEST_STACK and CONFIRM_DEPLOY:
    destroy.main(["--yes"])
else:
    destroy.main([]) if deploy.state_path(cfg).exists() else print("nothing deployed for", cfg.project, cfg.region)'''))

nb = nbf.v4.new_notebook(cells=cells)
nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
nb.metadata["language_info"] = {"name": "python"}
nbf.write(nb, OUT)
print(f"wrote {OUT}: {len(cells)} cells")
