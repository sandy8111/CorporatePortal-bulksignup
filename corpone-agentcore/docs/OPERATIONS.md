# Operating corpone-agentcore

## Observability (AgentCore Observability, CloudWatch Omni)

1. **Turn on CloudWatch Transaction Search once per account and region** so spans land in the `aws/spans`
   log group (needed by AgentCore Observability, Evaluations and CloudWatch Omni):
   ```bash
   aws xray update-trace-segment-destination --destination CloudWatchLogs --region $AWS_REGION
   ```
   (If it asks for a CloudWatch Logs resource policy, follow the CloudWatch "Transaction Search" setup page.)
2. **Assistant:** the image runs under `opentelemetry-instrument` (AWS Distro for OpenTelemetry); traces,
   logs and token metrics appear in CloudWatch → GenAI Observability → AgentCore and in CloudWatch Omni.
   Custom span attributes: `corpone.role`, `corpone.tools_available/allowed/used`, `corpone.memory_enabled`.
   No prompt text, token or email is put on a span by the assistant code.
3. **Portal:** install `portal/requirements-otel.txt` and set
   `CORPONE_OTEL_ENABLED=true`, `CORPONE_OTEL_SERVICE_NAME=corpone-portal`, and
   `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://localhost:4318/v1/traces` pointing at a CloudWatch agent or ADOT
   collector sidecar, which signs and forwards to CloudWatch. Query strings keep only parameter names, and the
   caller is recorded only as `ao`, `admin`, `api-client` or `anonymous`. `corpone.request_id` matches the
   portal log line and the `X-Request-ID` header.
4. **Behind a load balancer**, set `CORPONE_TRUSTED_PROXIES` to the load balancer's address ranges so sign-in
   rate limits and audit rows use the real client IP.
5. **CloudWatch Omni:** use the Omni web UI or the VS Code extension with your organisation's SSO (no AWS
   Console access needed for engineers). Suggested alerts: Gateway policy denies > 0 in `ENFORCE` mode
   (possible misuse), assistant error rate, `retryProvisioning` calls, evaluation scores dropping below the
   offline baseline, portal provisioning failures. Ask the AI assistant / AWS DevOps Agent investigation to start
   from a failing `corpone.request_id`.

## Policy rollout

- Deploy with `POLICY_MODE=LOG_ONLY`; decisions are logged but not enforced.
- Run `evals/run_offline_eval.py` for both roles; check the policy decision logs show the expected
  allow/deny for each tool (officer: only listPlans and validateUen allowed).
- Switch: `python infra/deploy.py --apply --policy-mode ENFORCE`.
- Incident: `python infra/deploy.py --apply --kill-switch on` denies every tool call (only effective in
  `ENFORCE`); `--kill-switch off` restores service.
- Policies live in `infra/policies/*.cedar`; `{{TARGET}}` and `{{GATEWAY_ARN}}` are filled in at deploy time.
  Each file is created with `validationMode=FAIL_ON_ANY_FINDINGS`, so an unsafe or unsatisfiable rule fails the
  deploy instead of reaching production.

## Evaluations

- **Online:** created by `deploy.py`, samples `EVAL_SAMPLING_PERCENT` (10%) of sessions with
  GoalSuccessRate, ToolSelectionAccuracy, Helpfulness and Correctness. Results go to CloudWatch (and Omni).
- **Offline / release gate:** `python evals/run_offline_eval.py --runtime-arn <arn> --batch --min-pass 0.9`
  with `OFFICER_TOKEN` and `OPS_TOKEN`. Local checks: expected tools used, forbidden tools not used (e.g. an
  officer prompt-injection must not retry provisioning), NRIC not echoed. `--batch` also starts an AgentCore
  batch evaluation with each scenario's assertions and expected tool trajectory. Non-zero exit blocks a release.
- Add a scenario to `evals/dataset.jsonl` for every incident or new tool; the tests check scenarios never expect
  a tool the role's policy denies.

## Users and the assistant page

- Users are admin-created only. Invite one from the notebook (phase 6b) or with
  `aws cognito-idp admin-create-user --user-pool-id <id> --username <email> --temporary-password <one-time>` plus
  `admin-add-user-to-group --group-name ops|officer`. Cognito's hosted page makes them set their own password.
- Turn on MFA before real use: `aws cognito-idp set-user-pool-mfa-config --user-pool-id <id> --software-token-mfa-configuration Enabled=true --mfa-configuration OPTIONAL`.
- Start the portal with `infra/build/portal-assistant-<project>-<region>.env` to switch `/assistant` on; the callback
  URL registered on the app client is that page (`ASSISTANT_URL`, default: `PORTAL_GATEWAY_URL` with `/assistant`
  instead of `/gateway`). If the portal moves, re-deploy so the callback URL matches.

## Privacy (PDPA)

- Region `ap-southeast-1` keeps Runtime, Memory, logs and traces in Singapore.
- Memory: short-term only, NRIC/FIN and emails masked before storage, 7-day expiry, actor is the token subject.
  Before enabling long-term memory: a privacy review, a namespace per officer, record expiry, and no
  cross-company sharing.
- The assistant's prompt tells it never to ask for or repeat NRIC numbers; `evals` checks this.

## Cost estimate (list prices, AWS AgentCore and CloudWatch Omni pricing pages)

Assumptions: 2,000 conversations a month × 8 turns = 16,000 runs; 3 tool calls per turn = 48,000 tool calls.

| Service | Price | Monthly |
|---|---|---|
| Runtime (microVM v1) | $0.0895 per vCPU-hour, $0.00945 per GB-hour; no CPU charge while waiting on I/O | ≈ $4 |
| Gateway | $0.005 per 1k tool calls, $0.025 per 1k searches, $0.02 per 100 tools | ≈ $0.75 |
| Identity | Free through Runtime or Gateway | $0 |
| Policy | $0.000025 per authorization | ≈ $1.20 |
| Memory (short-term) | $1 per GB ingested, $0.20 per GB retrieved, $0.10 per GB-month | < $0.50 |
| Observability / Omni | Spans $0.35/GB, logs $0.50/GB, storage $0.03/GB-month | ≈ $3–6 |
| Evaluations online (10%) | $0.0024 per 1k input, $0.012 per 1k output tokens | ≈ $17 (≈ $170 at 100%) |
| Evaluations offline | Batch: $0.0018 / $0.009 per 1k tokens | ≈ $1–2 per release |
| **AgentCore total** | | **≈ $30–35** (long-term memory, if enabled later, adds ≈ $12) |
| Bedrock model tokens | per your model's price | usually the largest item (≈ 100M input tokens at ~6k per turn) |

## Tear-down

`python infra/destroy.py` lists what will be deleted; `--yes` deletes it in reverse order (online evaluation,
runtime, memory, policies, target, gateway, policy engine, credential provider, IAM roles, Cognito pool).
The ECR repository and CloudWatch log groups are kept; delete them separately if no longer needed.
