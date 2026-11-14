"""Account-officer login with a one-time code sent by email."""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import AoSession, OtpChallenge
from app.ratelimit import SlidingWindowLimiter
from app.services import email as email_svc

SESSION_COOKIE = "corpone_session"
_ip_limiter = SlidingWindowLimiter(window_seconds=900)
IP_OTP_LIMIT = 20  # OTP requests per IP per 15 minutes


class OtpError(Exception):
    def __init__(self, message: str, status: int = 400, retry_after: int | None = None):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


def _utc(dt: datetime) -> datetime:
    """SQLite drops tzinfo; everything is stored as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _code_hash(email: str, code: str) -> str:
    key = get_settings().otp_pepper.encode()
    return hmac.new(key, f"{email}:{code}".encode(), hashlib.sha256).hexdigest()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _generate_code(email: str) -> str:
    s = get_settings()
    # Testing only (see Settings.dev_fixed_otp): fixed code for the one configured test account.
    if s.fixed_otp_email and email == s.fixed_otp_email:
        return s.dev_fixed_otp
    return f"{secrets.randbelow(10**6):06d}"


def request_otp(db: Session, email: str, remote_ip: str) -> None:
    s = get_settings()
    email = email.strip().lower()
    allowed, _, retry = _ip_limiter.hit(f"ip:{remote_ip}", IP_OTP_LIMIT)
    if not allowed:
        raise OtpError("Too many code requests from this network. Try again later.", 429, int(retry) + 1)

    now = _now()
    recent = db.scalars(select(OtpChallenge).where(
        OtpChallenge.email == email, OtpChallenge.created_at >= now - timedelta(minutes=15))
        .order_by(OtpChallenge.created_at.desc())).all()
    if recent and (now - _utc(recent[0].created_at)).total_seconds() < s.otp_resend_cooldown_seconds:
        wait = s.otp_resend_cooldown_seconds - int((now - _utc(recent[0].created_at)).total_seconds())
        raise OtpError(f"Please wait {wait}s before requesting another code.", 429, wait)
    if len(recent) >= s.otp_max_per_15min:
        raise OtpError("Too many codes requested for this email. Try again in 15 minutes.", 429, 900)

    # Only the newest code is valid.
    for ch in recent:
        if ch.consumed_at is None:
            ch.consumed_at = now
    code = _generate_code(email)
    db.add(OtpChallenge(email=email, code_hash=_code_hash(email, code),
                        expires_at=now + timedelta(seconds=s.otp_ttl_seconds)))
    db.commit()

    minutes = s.otp_ttl_seconds // 60
    email_svc.send(email_svc.build_message(
        email, f"Your CorpOne sign-in code: {code}",
        f"Your CorpOne Business sign-in code is {code}.\n\nIt expires in {minutes} minutes. "
        "If you did not request this, ignore this email. CorpOne will never ask you for this code.",
        f"<p>Your CorpOne Business sign-in code is</p><p style='font-size:28px;letter-spacing:6px'>"
        f"<b>{code}</b></p><p>It expires in {minutes} minutes. If you did not request this, ignore "
        "this email. CorpOne will never ask you for this code.</p>"))


def verify_otp(db: Session, email: str, code: str) -> str:
    """Check the code; on success returns a new session token (only its hash is stored)."""
    s = get_settings()
    email = email.strip().lower()
    code = code.strip()
    now = _now()
    ch = db.scalars(select(OtpChallenge).where(
        OtpChallenge.email == email, OtpChallenge.consumed_at.is_(None))
        .order_by(OtpChallenge.created_at.desc())).first()
    if ch is None or _utc(ch.expires_at) < now:
        raise OtpError("Code expired or not found. Request a new code.", 401)
    if ch.attempts >= s.otp_max_attempts:
        raise OtpError("Too many wrong attempts. Request a new code.", 401)
    ch.attempts += 1
    if not hmac.compare_digest(ch.code_hash, _code_hash(email, code)):
        if ch.attempts >= s.otp_max_attempts:
            ch.consumed_at = now
        db.commit()
        left = s.otp_max_attempts - ch.attempts
        raise OtpError(f"Incorrect code. {left} attempt(s) left." if left else
                       "Too many wrong attempts. Request a new code.", 401)
    ch.consumed_at = now
    token = secrets.token_urlsafe(32)
    db.add(AoSession(token_hash=_token_hash(token), email=email,
                     expires_at=now + timedelta(hours=s.session_ttl_hours)))
    db.commit()
    return token


def _lookup(db: Session, token: str | None) -> AoSession | None:
    if not token:
        return None
    sess = db.scalars(select(AoSession).where(AoSession.token_hash == _token_hash(token))).first()
    if sess is None or sess.revoked or _utc(sess.expires_at) < _now():
        return None
    return sess


def current_ao(db: Session = Depends(get_db),
               corpone_session: str | None = Cookie(default=None),
               authorization: str | None = Header(default=None)) -> AoSession:
    """Dependency: the logged-in account officer (cookie for the browser, Bearer for API use)."""
    token = corpone_session
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    sess = _lookup(db, token)
    if sess is None:
        raise HTTPException(401, "Please sign in with your email one-time code")
    return sess


def revoke(db: Session, sess: AoSession) -> None:
    sess.revoked = True
    db.commit()


def remote_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def purge_expired(db: Session) -> None:
    cutoff = _now() - timedelta(days=1)
    db.query(OtpChallenge).filter(OtpChallenge.created_at < cutoff).delete()
    db.query(AoSession).filter(AoSession.expires_at < cutoff).delete()
    db.commit()

