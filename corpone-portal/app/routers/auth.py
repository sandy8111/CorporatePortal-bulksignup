from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app import auth
from app.config import get_settings
from app.db import get_db
from app.models import AoSession

router = APIRouter(prefix="/api/auth", tags=["auth"])


class OtpRequest(BaseModel):
    email: EmailStr


class OtpVerify(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^\s*\d{6}\s*$")


def _fail(exc: auth.OtpError):
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    raise HTTPException(exc.status, str(exc), headers=headers)


@router.get("/options")
def login_options():
    """Lets the login page show a test-mode banner. The fixed code itself is never sent to the browser."""
    email = get_settings().fixed_otp_email
    return {"test_mode": True, "test_email": email} if email else {"test_mode": False}


@router.post("/otp/request", status_code=202)
def request_code(body: OtpRequest, request: Request, db: Session = Depends(get_db)):
    try:
        auth.request_otp(db, str(body.email), auth.remote_ip(request))
    except auth.OtpError as exc:
        _fail(exc)
    return {"sent": True, "expires_in": get_settings().otp_ttl_seconds}


@router.post("/otp/verify")
def verify_code(body: OtpVerify, response: Response, db: Session = Depends(get_db)):
    try:
        token = auth.verify_otp(db, str(body.email), body.code)
    except auth.OtpError as exc:
        _fail(exc)
    s = get_settings()
    response.set_cookie(auth.SESSION_COOKIE, token, max_age=s.session_ttl_hours * 3600,
                        httponly=True, secure=s.cookie_secure, samesite="strict", path="/")
    # The token is also returned for non-browser clients (send as "Authorization: Bearer").
    return {"email": str(body.email).lower(), "token": token, "expires_in": s.session_ttl_hours * 3600}


@router.get("/me")
def me(sess: AoSession = Depends(auth.current_ao)):
    return {"email": sess.email}


@router.post("/logout", status_code=204)
def logout(response: Response, sess: AoSession = Depends(auth.current_ao), db: Session = Depends(get_db)):
    auth.revoke(db, sess)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
