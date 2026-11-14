import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="corpone-test-")
os.environ.update({
    "CORPONE_DATABASE_URL": f"sqlite:///{_tmp}/test.db",
    "CORPONE_STORAGE_BACKEND": "s3",
    "CORPONE_S3_BUCKET": "corpone-test-contracts",
    "CORPONE_AWS_REGION": "ap-southeast-1",
    "CORPONE_EMAIL_BACKEND": "console",
    "CORPONE_OUTBOX_DIR": f"{_tmp}/outbox",
    "CORPONE_TELCO_API_MODE": "mock",
    "CORPONE_ADMIN_TOKEN": "test-admin",
    "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_DEFAULT_REGION": "ap-southeast-1",
})

import boto3  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from moto import mock_aws  # noqa: E402

import email as _email  # noqa: E402
from email import policy as _policy  # noqa: E402
import re  # noqa: E402

from app import auth  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.gateway import inbound  # noqa: E402
from app.gateway.outbound import gateway  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402

OUTBOX = f"{_tmp}/outbox"
ADMIN = {"X-Admin-Token": "test-admin"}
AO_EMAIL = "meiling@acme.sg"


def mails(subject_contains: str = "", to: str | None = None) -> list:
    out = []
    if not os.path.isdir(OUTBOX):
        return out
    for f in sorted(os.listdir(OUTBOX), key=lambda f: os.path.getmtime(os.path.join(OUTBOX, f))):
        with open(os.path.join(OUTBOX, f), "rb") as fh:
            m = _email.message_from_bytes(fh.read(), policy=_policy.default)
        if subject_contains in m["Subject"] and (to is None or m["To"] == to):
            out.append(m)
    return out


def last_otp(email_addr: str) -> str:
    return re.search(r"(\d{6})", mails("sign-in code", email_addr)[-1]["Subject"]).group(1)


def login(c, email_addr: str = AO_EMAIL) -> str:
    r = c.post("/api/auth/otp/request", json={"email": email_addr})
    assert r.status_code == 202, r.text
    r = c.post("/api/auth/otp/verify", json={"email": email_addr, "code": last_otp(email_addr)})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="ap-southeast-1")
        client.create_bucket(Bucket="corpone-test-contracts",
                             CreateBucketConfiguration={"LocationConstraint": "ap-southeast-1"})
        yield client


@pytest.fixture
def client(s3):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
    if os.path.isdir(OUTBOX):
        for f in os.listdir(OUTBOX):
            os.remove(os.path.join(OUTBOX, f))
    auth._ip_limiter.reset()
    inbound.limiter.reset()
    gateway.reset()
    gateway.sleep = lambda _s: None
    with TestClient(app) as c:
        login(c)
        yield c


@pytest.fixture
def anon(s3):
    """A client with no AO session (shares the DB reset done by `client` when both are used)."""
    with TestClient(app) as c:
        yield c
