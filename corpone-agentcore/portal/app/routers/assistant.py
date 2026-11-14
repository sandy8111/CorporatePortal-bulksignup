"""The /assistant page's API: where to sign in, and chat messages relayed to AgentCore Runtime.

The browser signs in with Cognito Managed Login (authorization code + PKCE) and sends its access token with
each message. This relay forwards that token unchanged; AgentCore Runtime verifies it (JWT authorizer) and
the Gateway and Policy check it again for every tool call, so the portal never decides who the user is here.
"""
import re
import urllib.parse

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import get_settings
from app.netutil import client_ip
from app.ratelimit import SlidingWindowLimiter

router = APIRouter(prefix="/api/assistant", tags=["assistant"])
limiter = SlidingWindowLimiter(window_seconds=60)
SESSION_RE = re.compile(r"^[A-Za-z0-9-]{33,100}$")   # AgentCore runtime session ids: 33-100 characters


class Message(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    session_id: str


def enabled() -> bool:
    s = get_settings()
    return all((s.assistant_runtime_arn, s.assistant_region, s.assistant_login_url, s.assistant_client_id))


def runtime_url() -> str:
    s = get_settings()
    if s.assistant_runtime_url:
        return s.assistant_runtime_url
    return (f"https://bedrock-agentcore.{s.assistant_region}.amazonaws.com/runtimes/"
            f"{urllib.parse.quote(s.assistant_runtime_arn, safe='')}/invocations?qualifier=DEFAULT")


def _post(url: str, body: dict, headers: dict) -> httpx.Response:   # replaced in tests
    return httpx.post(url, json=body, headers=headers, timeout=120)


@router.get("/config")
def config():
    s = get_settings()
    if not enabled():
        return {"enabled": False}
    return {"enabled": True, "login_url": s.assistant_login_url.rstrip("/"), "client_id": s.assistant_client_id,
            "scopes": "openid email profile"}


@router.post("/invoke")
def invoke(body: Message, request: Request, authorization: str = Header(default="")):
    if not enabled():
        raise HTTPException(503, "The assistant is not set up on this portal")
    if not authorization.lower().startswith("bearer ") or len(authorization) < 20:
        raise HTTPException(401, "Sign in to use the assistant")
    if not SESSION_RE.fullmatch(body.session_id):
        raise HTTPException(400, "session_id must be 33-100 letters, digits or dashes")
    ok, _, retry = limiter.hit(f"assistant:{client_ip(request)}", get_settings().assistant_rate_per_min)
    if not ok:
        raise HTTPException(429, "Too many messages; try again shortly", headers={"Retry-After": str(int(retry) + 1)})
    try:
        r = _post(runtime_url(), {"prompt": body.prompt},
                  {"Authorization": authorization, "Content-Type": "application/json",
                   "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": body.session_id})
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Assistant unavailable ({type(exc).__name__})")
    if r.status_code in (401, 403):
        raise HTTPException(401, "Your sign-in has expired; sign in again")
    if r.status_code >= 400:
        raise HTTPException(502, f"Assistant error (HTTP {r.status_code})")
    try:
        data = r.json()
    except ValueError:
        raise HTTPException(502, "Assistant returned an unreadable reply")
    meta = data.get("metadata", {}) if isinstance(data, dict) else {}
    return {"status": data.get("status"), "response": data.get("response") or data.get("error", ""),
            "role": meta.get("role"), "tools_used": meta.get("tools_used", [])}
