"""OpenTelemetry tracing for the portal (off unless CORPONE_OTEL_ENABLED=true).

Traces go to any OTLP/HTTP endpoint configured with the standard OTEL_EXPORTER_OTLP_* variables, e.g. the
CloudWatch OTLP endpoint (CloudWatch Omni / Application Signals) or a local ADOT collector.

Personal data never goes into a span:
* query strings are reduced to their parameter names (searches can contain an NRIC, number or email);
* only the kind of caller is recorded (ao / admin / api-client / anonymous), never the email;
* request and response bodies are never recorded (the FastAPI instrumentation doesn't capture them).
"""
import logging
import re
from urllib.parse import parse_qsl

from app.config import get_settings

log = logging.getLogger("corpone.telemetry")
_ATTRS_WITH_QUERY = ("http.target", "http.url", "url.full", "url.query")


def scrub_query(value: str) -> str:
    """'/api/x?q=S1234567D&limit=5' -> '/api/x?q=_&limit=_' (keys kept, values dropped)."""
    if "?" in value:
        base, _, query = value.partition("?")
        keys = [k for k, _ in parse_qsl(query, keep_blank_values=True)]
        return base + ("?" + "&".join(f"{k}=_" for k in keys) if keys else "")
    if "=" in value and "/" not in value:      # a bare query string (url.query)
        return "&".join(f"{k}=_" for k, _ in parse_qsl(value, keep_blank_values=True))
    return value


_NRIC = re.compile(r"\b[STFGM]\d{7}[A-Z]\b", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def scrub_text(value: str) -> str:
    """Mask NRIC/FIN and emails in free text such as exception messages."""
    return _EMAIL.sub("<email>", _NRIC.sub("<nric>", value))


def _server_request_hook(span, scope):
    if not span or not span.is_recording():
        return
    for key in _ATTRS_WITH_QUERY:
        val = getattr(span, "attributes", {}).get(key) if hasattr(span, "attributes") else None
        if isinstance(val, str):
            span.set_attribute(key, scrub_query(val))
    qs = scope.get("query_string", b"")
    if qs:
        span.set_attribute("url.query", scrub_query(qs.decode("latin-1")))


def caller_kind(caller: str) -> str:
    if caller in ("-", ""):
        return "anonymous"
    if caller == "admin":
        return "admin"
    if caller.startswith("api-client:"):
        return "api-client"
    return "ao"


def annotate_current_span(request_id: str, caller: str) -> None:
    """Called by the request log: puts the X-Request-ID and caller kind on the active span, if tracing is on."""
    try:
        from opentelemetry import trace
    except ImportError:
        return
    span = trace.get_current_span()
    if span and span.is_recording():
        span.set_attribute("corpone.request_id", request_id)
        span.set_attribute("corpone.caller_kind", caller_kind(caller))


def setup(app, exporter=None) -> bool:
    """Instrument the FastAPI app. Returns True when tracing is active. `exporter` overrides OTLP (tests)."""
    s = get_settings()
    if not s.otel_enabled:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning("CORPONE_OTEL_ENABLED is set but OpenTelemetry packages are missing; "
                    "install requirements-otel.txt. Tracing is off.")
        return False
    provider = TracerProvider(resource=Resource.create({"service.name": s.otel_service_name}))
    if exporter is not None:
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    else:
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    if exporter is None:
        trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, excluded_urls=r"healthz,/static/,favicon\.ico",
                                       server_request_hook=_server_request_hook, tracer_provider=provider)
    log.info("OpenTelemetry tracing on (service.name=%s)", s.otel_service_name)
    return True
