"""Mask personal data before it is stored in AgentCore Memory or written to a span.

Masked: Singapore NRIC/FIN (S/T/F/G/M + 7 digits + letter) and email addresses. Mobile line numbers and
ICCIDs are company line data and are kept; they are needed to talk about an order.
"""
import re

_NRIC = re.compile(r"\b[STFGM]\d{7}[A-Z]\b", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def redact(text: str) -> str:
    if not text:
        return text
    return _EMAIL.sub("<email>", _NRIC.sub("<nric>", text))
