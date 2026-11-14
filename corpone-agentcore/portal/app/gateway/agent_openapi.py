"""OpenAPI document of the partner API, cleaned for an AgentCore Gateway OpenAPI target.

The Gateway turns each operationId into an MCP tool. Its OpenAPI support excludes anyOf/oneOf/allOf and
spec-level security schemes, and needs a static server URL, so this module:
* keeps only the operations agents may call (AGENT_TOOLS; file uploads stay out);
* inlines every $ref and turns FastAPI's `anyOf: [X, null]` for optional fields into plain X;
* drops security schemes (the Gateway's outbound API-key credential provider authenticates instead);
* sets `servers` to the portal's public gateway URL.
"""
import copy

from app.gateway.inbound import gateway_app

# operationId -> who may use it (enforced by AgentCore Policy, see infra/policies)
AGENT_TOOLS = {
    "listPlans": "officer",
    "validateUen": "officer",
    "getInventorySummary": "ops",
    "checkBlacklist": "ops",
    "getAccountStatus": "ops",
    "retryProvisioning": "ops",
}
_COMPOSITION = ("anyOf", "oneOf", "allOf")
_DROP_KEYS = ("title",)
_AUTH_HEADERS = {"authorization", "x-api-key"}  # FastAPI adds titles to every property; they only add noise to tool specs


def _deref(node, components, seen=()):
    if isinstance(node, dict):
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            if name in seen:
                return {"type": "object"}
            return _deref(copy.deepcopy(components[name]), components, seen + (name,))
        out = {}
        for k, v in node.items():
            if k in _DROP_KEYS and isinstance(v, str):
                continue
            out[k] = _deref(v, components, seen)
        for comp in _COMPOSITION:
            if comp in out:
                options = [o for o in out.pop(comp) if o.get("type") != "null"]
                if len(options) == 1:
                    merged = {**options[0], **out}
                    out = merged
                else:
                    out.setdefault("type", "string")  # several real alternatives: describe as a string
        return out
    if isinstance(node, list):
        return [_deref(v, components, seen) for v in node]
    return node


def build(server_url: str) -> dict:
    """The agent-facing OpenAPI 3.0 document. server_url is the public base, e.g. https://portal.example.sg/gateway"""
    spec = gateway_app.openapi()
    components = spec.get("components", {}).get("schemas", {})
    paths = {}
    for path, ops in spec["paths"].items():
        kept = {}
        for method, op in ops.items():
            if op.get("operationId") not in AGENT_TOOLS:
                continue
            op = _deref(copy.deepcopy(op), components)
            op.pop("security", None)
            op["responses"] = {"200": op["responses"].get("200", {"description": "OK"}),
                               "400": {"description": "Invalid input"}, "404": {"description": "Not found"}}
            params = []
            for p in op.get("parameters", []):
                if p.get("in") == "header" and p["name"].lower() in _AUTH_HEADERS:
                    continue  # supplied by the Gateway's API-key credential provider, not by the model
                if p.get("in") == "header":
                    raise ValueError(f"{op['operationId']}: header parameter {p['name']} is not supported by the Gateway")
                params.append(p)
            if params:
                op["parameters"] = params
            else:
                op.pop("parameters", None)
            kept[method] = op
        if kept:
            paths[path] = kept
    missing = set(AGENT_TOOLS) - {op["operationId"] for ops in paths.values() for op in ops.values()}
    if missing:
        raise ValueError(f"operations not found in the partner API: {sorted(missing)}")
    return {
        "openapi": "3.0.3",
        "info": {"title": "CorpOne partner API (agent tools)", "version": spec["info"]["version"],
                 "description": "Tools for CorpOne AI assistants. Authorization is enforced by AgentCore Policy and "
                                "by the API key's scopes."},
        "servers": [{"url": server_url.rstrip("/")}],
        "paths": paths,
    }


def problems(doc: dict) -> list[str]:
    """Features the Gateway rejects, found anywhere in the document (empty list = OK)."""
    found = []

    def walk(node, where):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in _COMPOSITION or k == "$ref":
                    found.append(f"{where}: {k}")
                if k == "securitySchemes":
                    found.append(f"{where}: securitySchemes")
                walk(v, f"{where}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{where}[{i}]")

    walk(doc, "$")
    for path, ops in doc.get("paths", {}).items():
        for method, op in ops.items():
            if not op.get("operationId"):
                found.append(f"{method.upper()} {path}: no operationId")
    if not doc.get("servers") or "{" in doc["servers"][0]["url"]:
        found.append("servers: needs one static URL")
    return found
