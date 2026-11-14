"""The deployment as an ordered list of AWS API requests (pure: no AWS calls here).

Each Step builds its request from the config and the outputs of earlier steps, names the outputs it
produces (paths into the response), and, for resources AgentCore creates asynchronously, which status
to wait for. infra/deploy.py runs the steps; the tests validate every request against the real AWS API
models without calling AWS.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from corpone_infra.config import InfraConfig

ROOT = Path(__file__).resolve().parents[2]
POLICY_DIR = Path(__file__).resolve().parents[1] / "policies"
EVALUATORS = ["Builtin.GoalSuccessRate", "Builtin.ToolSelectionAccuracy", "Builtin.Helpfulness", "Builtin.Correctness"]


@dataclass
class Wait:
    operation: str                 # e.g. get_gateway
    id_param: str                  # request parameter name for the resource id
    id_output: str                 # which output holds the id
    status_path: str               # e.g. "status" or "memory.status"
    ready: tuple = ("READY",)
    failed: tuple = ("FAILED", "CREATE_FAILED")
    extra: dict = field(default_factory=dict)   # e.g. gatewayIdentifier for targets/policies


@dataclass
class Step:
    key: str
    service: str                   # boto3 client name
    operation: str                 # boto3 method name
    build: Callable[[dict], dict]  # outputs-so-far -> request params
    outputs: dict = field(default_factory=dict)   # output name -> dotted path in the response
    wait: Wait | None = None
    secret_params: tuple = ()      # params masked in plans and logs
    what: str = ""


def _arn(cfg, service, resource):
    return f"arn:aws:{service}:{cfg.region}:{cfg.account_id or '<account>'}:{resource}"


def trust(cfg, service="bedrock-agentcore.amazonaws.com"):
    cond = {"StringEquals": {"aws:SourceAccount": cfg.account_id or "<account>"}}
    if service == "bedrock-agentcore.amazonaws.com":
        cond["ArnLike"] = {"aws:SourceArn": _arn(cfg, "bedrock-agentcore", "*")}
    return json.dumps({"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Principal": {"Service": service}, "Action": "sts:AssumeRole", "Condition": cond}]})


def gateway_role_policy(cfg):
    n = cfg.names()
    pe = _arn(cfg, "bedrock-agentcore", f"policy-engine/{n['policy_engine']}*")
    gw = _arn(cfg, "bedrock-agentcore", f"gateway/{n['gateway']}*")
    return json.dumps({"Version": "2012-10-17", "Statement": [
        {"Sid": "PolicyEngineConfiguration", "Effect": "Allow", "Action": ["bedrock-agentcore:GetPolicyEngine"], "Resource": [pe]},
        {"Sid": "PolicyEngineAuthorization", "Effect": "Allow",
         "Action": ["bedrock-agentcore:AuthorizeAction", "bedrock-agentcore:PartiallyAuthorizeActions"], "Resource": [pe, gw]},
        {"Sid": "OutboundApiKey", "Effect": "Allow",
         "Action": ["bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetResourceApiKey"],
         "Resource": [_arn(cfg, "bedrock-agentcore", "workload-identity-directory/default"),
                      _arn(cfg, "bedrock-agentcore", "workload-identity-directory/default/workload-identity/*"),
                      _arn(cfg, "bedrock-agentcore", "token-vault/default"),
                      _arn(cfg, "bedrock-agentcore", f"token-vault/default/apikeycredentialprovider/{n['credential_provider']}")]},
        {"Sid": "ReadApiKeySecret", "Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"],
         "Resource": [_arn(cfg, "secretsmanager", "secret:bedrock-agentcore-identity!default/apikey/*")]},
    ]})


def runtime_role_policy(cfg, out):
    return json.dumps({"Version": "2012-10-17", "Statement": [
        {"Sid": "InvokeModel", "Effect": "Allow", "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
         "Resource": [f"arn:aws:bedrock:{cfg.region}::foundation-model/*", _arn(cfg, "bedrock", "inference-profile/*"),
                      "arn:aws:bedrock:*::foundation-model/*"]},
        {"Sid": "PullImage", "Effect": "Allow", "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
         "Resource": [_arn(cfg, "ecr", f"repository/{cfg.agent_image_uri.split('/', 1)[-1].split(':')[0] or '*'}")]},
        {"Sid": "EcrToken", "Effect": "Allow", "Action": ["ecr:GetAuthorizationToken"], "Resource": "*"},
        {"Sid": "Logs", "Effect": "Allow",
         "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams",
                    "logs:DescribeLogGroups"],
         "Resource": [_arn(cfg, "logs", "log-group:/aws/bedrock-agentcore/runtimes/*"), _arn(cfg, "logs", "log-group:*")]},
        {"Sid": "Traces", "Effect": "Allow",
         "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"],
         "Resource": "*"},
        {"Sid": "Metrics", "Effect": "Allow", "Action": ["cloudwatch:PutMetricData"], "Resource": "*",
         "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}}},
        {"Sid": "ShortTermMemory", "Effect": "Allow",
         "Action": ["bedrock-agentcore:CreateEvent", "bedrock-agentcore:ListEvents", "bedrock-agentcore:GetEvent"],
         "Resource": [out.get("memory_arn", _arn(cfg, "bedrock-agentcore", "memory/*"))]},
        {"Sid": "WorkloadIdentity", "Effect": "Allow",
         "Action": ["bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                    "bedrock-agentcore:GetWorkloadAccessTokenForUserId"],
         "Resource": [_arn(cfg, "bedrock-agentcore", "workload-identity-directory/default"),
                      _arn(cfg, "bedrock-agentcore", "workload-identity-directory/default/workload-identity/*")]},
    ]})


def eval_role_policy(cfg):
    return json.dumps({"Version": "2012-10-17", "Statement": [
        {"Sid": "JudgeModel", "Effect": "Allow", "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
         "Resource": [f"arn:aws:bedrock:{cfg.region}::foundation-model/*", _arn(cfg, "bedrock", "inference-profile/*")]},
        {"Sid": "ReadTraces", "Effect": "Allow",
         "Action": ["logs:GetLogEvents", "logs:FilterLogEvents", "logs:StartQuery", "logs:GetQueryResults",
                    "logs:DescribeLogGroups", "logs:DescribeLogStreams", "logs:DescribeIndexPolicies", "logs:PutIndexPolicy"],
         "Resource": [_arn(cfg, "logs", "log-group:*")]},
        {"Sid": "WriteResults", "Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
         "Resource": [_arn(cfg, "logs", "log-group:/aws/bedrock-agentcore/evaluations/*")]},
        {"Sid": "Metrics", "Effect": "Allow", "Action": ["cloudwatch:PutMetricData"], "Resource": "*"},
        {"Sid": "XRayRead", "Effect": "Allow", "Action": ["xray:GetTraceSummaries", "xray:BatchGetTraces"], "Resource": "*"},
    ]})


def render_policy(text: str, cfg: InfraConfig, gateway_arn: str) -> str:
    return text.replace("{{TARGET}}", cfg.target_name).replace("{{GATEWAY_ARN}}", gateway_arn)


def policy_files(optional=False) -> list[Path]:
    return sorted((POLICY_DIR / "optional" if optional else POLICY_DIR).glob("*.cedar"))


def openapi_payload(cfg) -> str:
    """The partner API as Gateway tools, generated from the portal code (portal/app/gateway/agent_openapi.py)."""
    import sys
    sys.path.insert(0, str(ROOT / "portal"))
    from app.gateway.agent_openapi import build, problems
    doc = build(cfg.portal_gateway_url)
    issues = problems(doc)
    if issues:
        raise ValueError("agent OpenAPI is not Gateway-compatible: " + "; ".join(issues))
    return json.dumps(doc)


def steps(cfg: InfraConfig) -> list[Step]:
    n = cfg.names()
    discovery = lambda o: (f"https://cognito-idp.{cfg.region}.amazonaws.com/{o['user_pool_id']}"
                           "/.well-known/openid-configuration")
    jwt = lambda o: {"customJWTAuthorizer": {"discoveryUrl": discovery(o), "allowedClients": [o["app_client_id"]]}}
    tags = {"project": cfg.project}

    out = [
        # ---------------------------------------------------------------- Identity: who the users are (Cognito)
        Step("user_pool", "cognito-idp", "create_user_pool", lambda o: {
            "PoolName": n["user_pool"], "UsernameAttributes": ["email"], "AutoVerifiedAttributes": ["email"],
            "MfaConfiguration": "OFF", "UserPoolTags": tags,   # turn on TOTP MFA with set_user_pool_mfa_config
            "Policies": {"PasswordPolicy": {"MinimumLength": 12, "RequireUppercase": True, "RequireLowercase": True,
                                            "RequireNumbers": True, "RequireSymbols": True}},
            "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True}},
            {"user_pool_id": "UserPool.Id", "user_pool_arn": "UserPool.Arn"},
            what="Cognito user pool for assistant users (admin-created only)"),
        Step("group_officer", "cognito-idp", "create_group", lambda o: {
            "GroupName": "officer", "UserPoolId": o["user_pool_id"],
            "Description": "Corporate account officers: plans and UEN checks"}, what="group: officer"),
        Step("group_ops", "cognito-idp", "create_group", lambda o: {
            "GroupName": "ops", "UserPoolId": o["user_pool_id"],
            "Description": "CorpOne operations: inventory, blacklist, account status, retry provisioning"}, what="group: ops"),
        Step("app_client", "cognito-idp", "create_user_pool_client", lambda o: {
            "UserPoolId": o["user_pool_id"], "ClientName": n["app_client"], "GenerateSecret": False,
            "ExplicitAuthFlows": ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH",
                                  "ALLOW_ADMIN_USER_PASSWORD_AUTH"],   # server-side only, needs IAM: smoke tests and evals
            "AccessTokenValidity": 60, "IdTokenValidity": 60, "RefreshTokenValidity": 8,
            "TokenValidityUnits": {"AccessToken": "minutes", "IdToken": "minutes", "RefreshToken": "hours"},
            "PreventUserExistenceErrors": "ENABLED",
            # Managed Login (hosted page) for the portal's /assistant page: authorization code + PKCE, no secret
            "AllowedOAuthFlowsUserPoolClient": True, "AllowedOAuthFlows": ["code"],
            "AllowedOAuthScopes": ["openid", "email", "profile"], "SupportedIdentityProviders": ["COGNITO"],
            "CallbackURLs": [cfg.assistant_url], "LogoutURLs": [cfg.assistant_url]},
            {"app_client_id": "UserPoolClient.ClientId"},
            what=f"public app client (SRP / hosted login with PKCE, 60-minute tokens, returns to {cfg.assistant_url})"),
        Step("login_domain", "cognito-idp", "create_user_pool_domain", lambda o: {
            "Domain": o["login_domain"], "UserPoolId": o["user_pool_id"], "ManagedLoginVersion": 2},
            what="Cognito Managed Login domain (hosted sign-in page; self sign-up stays off)"),
        Step("login_branding", "cognito-idp", "create_managed_login_branding", lambda o: {
            "UserPoolId": o["user_pool_id"], "ClientId": o["app_client_id"], "UseCognitoProvidedValues": True},
            {"login_branding_id": "ManagedLoginBranding.ManagedLoginBrandingId"},
            what="Managed Login branding (Cognito default style) for the app client"),

        # ---------------------------------------------------------------- Gateway execution role
        Step("gateway_role", "iam", "create_role", lambda o: {
            "RoleName": n["gateway_role"], "AssumeRolePolicyDocument": trust(cfg),
            "Description": "AgentCore Gateway: Policy authorization and outbound API key", "Tags": [{"Key": "project", "Value": cfg.project}]},
            {"gateway_role_arn": "Role.Arn"}, what="IAM role assumed by the Gateway"),
        Step("gateway_role_policy", "iam", "put_role_policy", lambda o: {
            "RoleName": n["gateway_role"], "PolicyName": "agentcore-gateway", "PolicyDocument": gateway_role_policy(cfg)},
            what="Gateway role permissions (Policy engine, API key credential)"),

        # ---------------------------------------------------------------- Identity: outbound credential vault
        Step("credential_provider", "bedrock-agentcore-control", "create_api_key_credential_provider", lambda o: {
            "name": n["credential_provider"], "apiKey": o["portal_api_key"], "tags": tags},
            {"credential_provider_arn": "credentialProviderArn"}, secret_params=("apiKey",),
            what="AgentCore Identity: stores the portal API key (cpk_...) in the token vault"),

        # ---------------------------------------------------------------- Policy engine
        Step("policy_engine", "bedrock-agentcore-control", "create_policy_engine", lambda o: {
            "name": n["policy_engine"], "description": "CorpOne assistant tool authorization (Cedar)", "tags": tags},
            {"policy_engine_id": "policyEngineId", "policy_engine_arn": "policyEngineArn"},
            Wait("get_policy_engine", "policyEngineId", "policy_engine_id", "status", ("ACTIVE",)),
            what="AgentCore Policy engine"),

        # ---------------------------------------------------------------- Gateway
        Step("gateway", "bedrock-agentcore-control", "create_gateway", lambda o: {
            "name": n["gateway"], "description": "CorpOne partner API as MCP tools", "roleArn": o["gateway_role_arn"],
            "protocolType": "MCP",
            "protocolConfiguration": {"mcp": {"searchType": "SEMANTIC", "instructions":
                "CorpOne corporate mobile-line tools. Read-only except retryProvisioning (ops only)."}},
            "authorizerType": "CUSTOM_JWT", "authorizerConfiguration": jwt(o),
            "policyEngineConfiguration": {"arn": o["policy_engine_arn"], "mode": cfg.policy_mode},
            "tags": tags},
            {"gateway_id": "gatewayId", "gateway_arn": "gatewayArn", "gateway_url": "gatewayUrl"},
            Wait("get_gateway", "gatewayIdentifier", "gateway_id", "status"),
            what=f"AgentCore Gateway (MCP, Cognito JWT, Policy {cfg.policy_mode})"),
        Step("gateway_target", "bedrock-agentcore-control", "create_gateway_target", lambda o: {
            "gatewayIdentifier": o["gateway_id"], "name": cfg.target_name,
            "description": "CorpOne portal /gateway/v1 (agent tools only; uploads excluded)",
            "targetConfiguration": {"mcp": {"openApiSchema": {"inlinePayload": o["openapi"]}}},
            "credentialProviderConfigurations": [{
                "credentialProviderType": "API_KEY",
                "credentialProvider": {"apiKeyCredentialProvider": {
                    "providerArn": o["credential_provider_arn"], "credentialParameterName": "X-API-Key",
                    "credentialLocation": "HEADER"}}}]},
            {"target_id": "targetId"},
            Wait("get_gateway_target", "targetId", "target_id", "status", extra={"gatewayIdentifier": "gateway_id"}),
            what="Gateway target: portal OpenAPI with the API key from Identity"),
    ]

    # ---------------------------------------------------------------- Cedar policies (need the gateway ARN)
    for path in policy_files():
        stem = path.stem
        out.append(Step(f"policy_{stem}", "bedrock-agentcore-control", "create_policy",
                        (lambda p: lambda o: {
                            "policyEngineId": o["policy_engine_id"], "name": p.stem.split("_", 1)[1][:48].replace("-", "_"),
                            "description": f"from infra/policies/{p.name}",
                            "definition": {"cedar": {"statement": render_policy(p.read_text(), cfg, o["gateway_arn"])}},
                            "validationMode": "FAIL_ON_ANY_FINDINGS"})(path),
                        {f"policy_{stem}_id": "policyId"},
                        Wait("get_policy", "policyId", f"policy_{stem}_id", "status", ("ACTIVE",),
                             extra={"policyEngineId": "policy_engine_id"}),
                        what=f"Cedar policy {path.name}"))

    out += [
        # ---------------------------------------------------------------- Memory (short-term only)
        Step("memory", "bedrock-agentcore-control", "create_memory", lambda o: {
            "name": n["memory"], "eventExpiryDuration": cfg.memory_expiry_days,
            "description": "Short-term conversation memory; no long-term strategies (PII review pending)", "tags": tags},
            {"memory_id": "memory.id", "memory_arn": "memory.arn"},
            Wait("get_memory", "memoryId", "memory_id", "memory.status", ("ACTIVE",)),
            what=f"AgentCore Memory, short-term only, events expire after {cfg.memory_expiry_days} days"),

        # ---------------------------------------------------------------- Runtime
        Step("runtime_role", "iam", "create_role", lambda o: {
            "RoleName": n["runtime_role"], "AssumeRolePolicyDocument": trust(cfg),
            "Description": "AgentCore Runtime: CorpOne assistant", "Tags": [{"Key": "project", "Value": cfg.project}]},
            {"runtime_role_arn": "Role.Arn"}, what="IAM role for the assistant runtime"),
        Step("runtime_role_policy", "iam", "put_role_policy", lambda o: {
            "RoleName": n["runtime_role"], "PolicyName": "agentcore-runtime", "PolicyDocument": runtime_role_policy(cfg, o)},
            what="Runtime permissions (model, logs/traces, memory events)"),
        Step("runtime", "bedrock-agentcore-control", "create_agent_runtime", lambda o: {
            "agentRuntimeName": n["runtime"], "description": "CorpOne assistant (Strands)",
            "agentRuntimeArtifact": {"containerConfiguration": {"containerUri": cfg.agent_image_uri or "<ecr-image-uri>"}},
            "roleArn": o["runtime_role_arn"], "networkConfiguration": {"networkMode": "PUBLIC"},
            "protocolConfiguration": {"serverProtocol": "HTTP"},
            "authorizerConfiguration": jwt(o),
            "requestHeaderConfiguration": {"requestHeaderAllowlist": ["Authorization"]},
            "lifecycleConfiguration": {"idleRuntimeSessionTimeout": 900, "maxLifetime": 3600},
            "environmentVariables": {"GATEWAY_URL": o["gateway_url"], "MODEL_ID": cfg.model_id or "<model-id>",
                                     "MEMORY_ID": o["memory_id"], "AWS_REGION": cfg.region,
                                     "GATEWAY_TARGET_NAME": cfg.target_name, "MEMORY_HISTORY_TURNS": "6"},
            "tags": tags},
            {"runtime_id": "agentRuntimeId", "runtime_arn": "agentRuntimeArn"},
            Wait("get_agent_runtime", "agentRuntimeId", "runtime_id", "status"),
            what="AgentCore Runtime hosting the assistant (Cognito JWT, Authorization header passed through)"),

        # ---------------------------------------------------------------- Evaluations (online, sampled)
        Step("eval_role", "iam", "create_role", lambda o: {
            "RoleName": n["eval_role"], "AssumeRolePolicyDocument": trust(cfg),
            "Description": "AgentCore Evaluations: CorpOne assistant", "Tags": [{"Key": "project", "Value": cfg.project}]},
            {"eval_role_arn": "Role.Arn"}, what="IAM role for evaluations"),
        Step("eval_role_policy", "iam", "put_role_policy", lambda o: {
            "RoleName": n["eval_role"], "PolicyName": "agentcore-evaluations", "PolicyDocument": eval_role_policy(cfg)},
            what="Evaluation permissions (judge model, read traces, write results)"),
        Step("online_eval", "bedrock-agentcore-control", "create_online_evaluation_config", lambda o: {
            "onlineEvaluationConfigName": n["online_eval"],
            "description": f"Sample {cfg.eval_sampling_percent:g}% of assistant sessions",
            "rule": {"samplingConfig": {"samplingPercentage": cfg.eval_sampling_percent},
                     "sessionConfig": {"sessionTimeoutMinutes": 15}},
            "dataSourceConfig": {"cloudWatchLogs": {
                "logGroupNames": ["aws/spans", f"/aws/bedrock-agentcore/runtimes/{o['runtime_id']}-DEFAULT"],
                "serviceNames": [f"{n['runtime']}.DEFAULT"]}},
            "evaluators": [{"evaluatorId": e} for e in EVALUATORS],
            "evaluationExecutionRoleArn": o["eval_role_arn"], "enableOnCreate": True, "tags": tags},
            {"online_eval_id": "onlineEvaluationConfigId", "online_eval_arn": "onlineEvaluationConfigArn"},
            what=f"Online evaluation on {cfg.eval_sampling_percent:g}% of sessions ({', '.join(EVALUATORS)})"),
    ]
    return out


def placeholder_outputs(cfg: InfraConfig) -> dict:
    """Stand-in outputs for dry runs and tests (realistic shapes so length rules still apply)."""
    acct = cfg.account_id or "111122223333"
    return {
        "login_domain": cfg.login_domain,
        "portal_api_key": "cpk_dryrun00_" + "x" * 32,
        "openapi": openapi_payload(cfg) if cfg.portal_gateway_url.startswith("https://") else "{}",
        "user_pool_id": f"{cfg.region}_AbCdEf123", "user_pool_arn": f"arn:aws:cognito-idp:{cfg.region}:{acct}:userpool/x",
        "app_client_id": "1example23456789abcdefghij",
        "gateway_role_arn": f"arn:aws:iam::{acct}:role/{cfg.names()['gateway_role']}",
        "runtime_role_arn": f"arn:aws:iam::{acct}:role/{cfg.names()['runtime_role']}",
        "eval_role_arn": f"arn:aws:iam::{acct}:role/{cfg.names()['eval_role']}",
        "credential_provider_arn": f"arn:aws:bedrock-agentcore:{cfg.region}:{acct}:token-vault/default/apikeycredentialprovider/x",
        "policy_engine_id": f"{cfg.names()['policy_engine']}-abcde12345",
        "policy_engine_arn": f"arn:aws:bedrock-agentcore:{cfg.region}:{acct}:policy-engine/{cfg.names()['policy_engine']}-abcde12345",
        "gateway_id": f"{cfg.names()['gateway']}-abcde12345",
        "gateway_arn": f"arn:aws:bedrock-agentcore:{cfg.region}:{acct}:gateway/{cfg.names()['gateway']}-abcde12345",
        "gateway_url": f"https://{cfg.names()['gateway']}-abcde12345.gateway.bedrock-agentcore.{cfg.region}.amazonaws.com/mcp",
        "memory_id": f"{cfg.names()['memory']}-AbCdE12345", "memory_arn": f"arn:aws:bedrock-agentcore:{cfg.region}:{acct}:memory/x",
        "runtime_id": f"{cfg.names()['runtime']}-AbCdE12345",
    }
