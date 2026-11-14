"""MCP server exposing the CorpOne gateway to AI agents (Claude, Bedrock AgentCore, etc.).

It holds a gateway API key and calls /gateway/v1, so every tool call is scoped, rate-limited
and audited by the gateway exactly like any other API client.

    CORPONE_GATEWAY_URL=http://localhost:8000/gateway CORPONE_GATEWAY_API_KEY=cpk_... \
        python -m mcp_server.server                 # stdio (Claude Desktop / Claude Code)
    python -m mcp_server.server --http              # streamable HTTP on :8000/mcp (AgentCore Runtime)
"""
import os
import sys

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("corpone-gateway", host="0.0.0.0", port=int(os.environ.get("MCP_PORT", "8000")),
              stateless_http=True)


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=os.environ.get("CORPONE_GATEWAY_URL", "http://localhost:8000/gateway"),
        headers={"Authorization": f"Bearer {os.environ.get('CORPONE_GATEWAY_API_KEY', '')}"},
        timeout=30,
    )


def _call(method: str, path: str, **kw) -> dict | list:
    with _client() as c:
        r = c.request(method, path, **kw)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        return {"error": f"HTTP {r.status_code}", "detail": detail}
    return r.json()


@mcp.tool()
def list_plans() -> list | dict:
    """List CorpOne corporate mobile plans with code, monthly price (SGD) and data allowance."""
    return _call("GET", "/v1/plans")


@mcp.tool()
def validate_uen(uen: str) -> dict:
    """Check that a Singapore UEN (business registration number) is well-formed and whether the company is blacklisted."""
    return _call("GET", f"/v1/validate/uen/{uen}")


@mcp.tool()
def check_blacklist(subject_type: str, id_type: str, id_value: str, on_date: str | None = None) -> dict:
    """Check whether a company or account officer is blacklisted (today, or on on_date YYYY-MM-DD).

    subject_type: COMPANY or ACCOUNT_OFFICER. id_type: UEN for companies; NRIC, FIN or PASSPORT for officers.
    """
    body = {"subject_type": subject_type, "id_type": id_type, "id_value": id_value}
    if on_date:
        body["on_date"] = on_date
    return _call("POST", "/v1/blacklist/check", json=body)


@mcp.tool()
def inventory_summary() -> list | dict:
    """SIM and number inventory per company category, including how many paired lines are ready to allocate."""
    return _call("GET", "/v1/inventory/summary")


@mcp.tool()
def get_account_status(account_number: str) -> dict:
    """Corporate account details and per-line provisioning status (network, billing, eSIM QR sent)."""
    return _call("GET", f"/v1/accounts/{account_number}")


@mcp.tool()
def retry_provisioning(account_number: str) -> dict:
    """Retry network/billing provisioning for lines of an account that failed or are pending. Safe to repeat."""
    return _call("POST", f"/v1/accounts/{account_number}/retry-provisioning")


if __name__ == "__main__":
    mcp.run(transport="streamable-http" if "--http" in sys.argv else "stdio")
