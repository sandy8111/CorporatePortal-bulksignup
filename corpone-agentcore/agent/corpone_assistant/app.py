"""AgentCore Runtime entrypoint for the CorpOne assistant.

Request:  POST /invocations  {"prompt": "..."}   with  Authorization: Bearer <Cognito access token>
          and the runtime session header (X-Amzn-Bedrock-AgentCore-Runtime-Session-Id) for conversation memory.
Response: {"status": "success", "response": "...", "metadata": {"role": ..., "tools_used": [...], ...}}

Identity: the Runtime's JWT authorizer verifies the caller's token; the same token is forwarded to the
Gateway, whose JWT authorizer verifies it again and whose Policy engine (Cedar) authorizes every tool call
for that user. The portal API key lives in AgentCore Identity and is added by the Gateway; this code never
sees it.
"""
import logging

from corpone_assistant import config as cfg
from corpone_assistant import identity, prompts, tools
from corpone_assistant.memory import ShortTermMemory
from corpone_assistant.redact import redact

log = logging.getLogger("corpone.assistant")

try:  # optional at import time so unit tests run without the Runtime SDK
    from opentelemetry import trace
    tracer = trace.get_tracer("corpone.assistant")
except ImportError:  # pragma: no cover
    tracer = None


class _NoSpan:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def set_attribute(self, *a):
        pass


def _span(name):
    return tracer.start_as_current_span(name) if tracer else _NoSpan()


def mcp_client(gateway_url: str, token: str):
    from mcp.client.streamable_http import streamablehttp_client
    from strands.tools.mcp import MCPClient
    return MCPClient(lambda: streamablehttp_client(url=gateway_url, headers={"Authorization": f"Bearer {token}"}))


def make_agent(conf, system_prompt: str, agent_tools: list, history: list):
    from strands import Agent
    from strands.models import BedrockModel
    model = BedrockModel(model_id=conf.model_id, region_name=conf.region, max_tokens=conf.max_tokens)
    return Agent(model=model, tools=agent_tools, system_prompt=system_prompt, messages=history,
                 callback_handler=None)


def memory_client(conf):
    import boto3
    return ShortTermMemory(boto3.client("bedrock-agentcore", region_name=conf.region), conf.memory_id)


def tools_used(agent) -> list[str]:
    used = []
    for msg in getattr(agent, "messages", []):
        if msg.get("role") == "assistant":
            for block in msg.get("content", []):
                if "toolUse" in block:
                    used.append(tools.plain_name(block["toolUse"].get("name", "")))
    return used


def handle(payload: dict, headers: dict | None, session_id: str | None, *, conf=None,
           connect=mcp_client, build_agent=make_agent, memory=None) -> dict:
    """The whole request, with dependencies injectable for tests."""
    conf = conf or cfg.load()
    prompt = (payload or {}).get("prompt", "").strip()
    if (payload or {}).get("warmup"):
        return {"status": "warmed"}
    if not prompt:
        return {"status": "error", "error": "No prompt provided"}
    token = identity.bearer(headers)
    if not token:
        return {"status": "error", "error": "Missing bearer token"}
    if not conf.gateway_url or not conf.model_id:
        return {"status": "error", "error": "Assistant is not configured (GATEWAY_URL / MODEL_ID)"}
    c = identity.claims(token)
    role, actor = identity.role(c), identity.actor_id(c)
    memory = memory if memory is not None else memory_client(conf)

    with _span("corpone.assistant.invocation") as span:
        span.set_attribute("corpone.role", role)
        span.set_attribute("corpone.memory_enabled", memory.enabled)
        client = connect(conf.gateway_url, token)
        with client:
            available = client.list_tools_sync()
            allowed = tools.for_role(available, role)
            span.set_attribute("corpone.tools_available", len(available))
            span.set_attribute("corpone.tools_allowed", len(allowed))
            history = memory.history(actor, session_id, conf.history_turns)
            agent = build_agent(conf, prompts.for_role(role), allowed, history)
            result = agent(prompt)
            text = str(result.message["content"][0]["text"]) if hasattr(result, "message") else str(result)
            used = tools_used(agent)
            span.set_attribute("corpone.tools_used", ",".join(used))
        try:
            memory.save_turn(actor, session_id, prompt, text)
        except Exception as exc:  # memory must never break the answer
            log.warning("memory save failed: %s", type(exc).__name__)
    return {"status": "success", "response": text,
            "metadata": {"role": role, "tools_used": used, "tools_allowed": sorted(tools.plain_name(t.tool_name) for t in allowed),
                         "session_id": session_id, "memory": memory.enabled, "model": conf.model_id}}


def create_app():
    from bedrock_agentcore.runtime import BedrockAgentCoreApp
    app = BedrockAgentCoreApp()

    @app.entrypoint
    def invoke(payload, context):
        try:
            return handle(payload, getattr(context, "request_headers", None), getattr(context, "session_id", None))
        except Exception as exc:
            log.error("assistant error type=%s", type(exc).__name__)
            return {"status": "error", "error": redact(f"{type(exc).__name__}: {exc}")[:300]}

    return app


if __name__ == "__main__":
    create_app().run()
