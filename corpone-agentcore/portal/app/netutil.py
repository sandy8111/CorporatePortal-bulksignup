"""Client IP behind trusted proxies.

Only X-Forwarded-For entries added by proxies listed in CORPONE_TRUSTED_PROXIES are believed. Walking the
header from the right, the first address that is not a trusted proxy is the client. A spoofed header from
the client itself sits further left and is ignored.
"""
import ipaddress
from functools import lru_cache

from starlette.requests import Request

from app.config import get_settings


@lru_cache(maxsize=8)
def _networks(spec: str) -> tuple:
    nets = []
    for part in (p.strip() for p in spec.split(",")):
        if part:
            nets.append(ipaddress.ip_network(part, strict=False))
    return tuple(nets)


def _trusted(addr: str, nets: tuple) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in n for n in nets)


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else ""
    nets = _networks(get_settings().trusted_proxies)
    if not nets or not _trusted(peer, nets):
        return peer
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    for hop in reversed(hops):
        if not _trusted(hop, nets):
            return hop
    return hops[0] if hops else peer
