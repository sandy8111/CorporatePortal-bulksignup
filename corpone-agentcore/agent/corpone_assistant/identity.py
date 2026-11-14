"""Who is calling: read from the Cognito access token that AgentCore Runtime has already verified.

The Runtime's JWT authorizer validates the token before the request reaches this code, so here it is only
decoded (not re-verified) to pick the role and the memory actor. Authorization itself is enforced again by
AgentCore Policy at the Gateway on every tool call.
"""
import base64
import json

ROLES = ("ops", "officer")


def claims(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError):
        return {}


def bearer(headers: dict | None) -> str:
    for k, v in (headers or {}).items():
        if k.lower() == "authorization" and v.lower().startswith("bearer "):
            return v[7:].strip()
    return ""


def role(c: dict) -> str:
    groups = c.get("cognito:groups") or []
    if isinstance(groups, str):
        groups = [groups]
    return "ops" if "ops" in groups else "officer"   # least privilege by default


def actor_id(c: dict) -> str:
    """Stable, non-personal memory actor: the token subject (a UUID), never the email."""
    return c.get("sub") or "anonymous"
