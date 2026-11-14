"""Contract agreement storage (S3 in AWS, local disk for development)."""
import os
import re
import uuid
from datetime import datetime, timezone

import boto3

from app.config import get_settings

ALLOWED_CONTRACT_TYPES = {
    "application/pdf": (".pdf", b"%PDF-"),
}


class ContractRejected(ValueError):
    pass


def check_contract(filename: str, content_type: str, data: bytes) -> str:
    settings = get_settings()
    if not data:
        raise ContractRejected("Contract file is empty")
    if len(data) > settings.max_contract_bytes:
        raise ContractRejected(f"Contract exceeds {settings.max_contract_bytes // (1024 * 1024)} MB")
    spec = ALLOWED_CONTRACT_TYPES.get(content_type)
    if not spec or not data.startswith(spec[1]):
        raise ContractRejected("Contract must be a PDF document")
    return spec[0]


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(name or "contract"))[:100]


def store_contract(uen: str, signup_id: str, filename: str, content_type: str, data: bytes) -> str:
    """Validate and store a contract; returns the storage key."""
    check_contract(filename, content_type, data)
    settings = get_settings()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    key = f"contracts/{uen}/{signup_id}/{stamp}-{uuid.uuid4().hex[:8]}-{_safe_name(filename)}"

    if settings.storage_backend == "s3":
        boto3.client("s3", region_name=settings.aws_region).put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            ServerSideEncryption="AES256",
            Metadata={"uen": uen, "signup-id": signup_id},
        )
    else:
        path = os.path.join(settings.local_upload_dir, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
    return key
