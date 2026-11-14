"""One log line per API call: method, path, status, time taken, caller and a request ID.

Request and response bodies are never logged (they hold OTP codes, NRIC numbers and contracts).
The request ID is returned in the X-Request-ID header so a call seen in the browser's Network tab
can be matched to its line in the server log.
"""
import logging
import re
import secrets
import time

log = logging.getLogger("corpone.api")

_SKIP = re.compile(r"^(/ports/\d+)?(/static/|/healthz$|/favicon\.ico$)")
_INCOMING_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def caller(state: dict) -> str:
    """Who made the call, as recorded on request.state by the auth dependencies."""
    if state.get("ao_email"):
        return state["ao_email"]
    if state.get("admin"):
        return "admin"
    if state.get("client_id"):
        return f"api-client:{state['client_id']}"
    return "-"


class RequestLog:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or _SKIP.match(scope["path"]):
            return await self.app(scope, receive, send)

        incoming = dict(scope.get("headers") or []).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _INCOMING_ID.fullmatch(incoming) else secrets.token_hex(4)
        state = scope.setdefault("state", {})  # shared with request.state, including mounted apps
        state["request_id"] = request_id
        status = 500
        start = time.perf_counter()

        async def send_with_id(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = [*message.get("headers", []), (b"x-request-id", request_id.encode())]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            ms = (time.perf_counter() - start) * 1000
            query = re.sub(r"(^|&)_ts=\d+", "", scope.get("query_string", b"").decode("latin-1")).lstrip("&")
            path = scope["path"] + (f"?{query}" if query else "")
            level = logging.ERROR if status >= 500 else logging.WARNING if status >= 400 else logging.INFO
            log.log(level, "%s %s -> %s %.0fms by=%s id=%s",
                    scope["method"], path, status, ms, caller(state), request_id)
