from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CORPONE_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/corpone.db"

    storage_backend: Literal["s3", "local"] = "local"
    s3_bucket: str = "corpone-contracts"
    aws_region: str = "ap-southeast-1"
    local_upload_dir: str = "./data/uploads"
    max_contract_bytes: int = 10 * 1024 * 1024

    email_backend: Literal["console", "smtp", "ses"] = "console"
    email_from: str = "noreply@corpone.sg"
    outbox_dir: str = "./data/outbox"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""

    default_smdp_address: str = "smdp.corpone.sg"

    telco_api_mode: Literal["mock", "http"] = "mock"
    network_api_base_url: str = "https://network-api.corpone.internal"
    billing_api_base_url: str = "https://billing-api.corpone.internal"
    telco_api_key: str = ""
    telco_api_timeout: float = 15.0

    admin_token: str = "change-me"

    # --- AO email-OTP login
    otp_pepper: str = "change-me-otp-pepper"   # HMAC key for stored OTP hashes; set per environment
    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 5
    otp_resend_cooldown_seconds: int = 30
    otp_max_per_15min: int = 5
    session_ttl_hours: int = 8
    cookie_secure: bool = False                 # set true behind HTTPS
    # TESTING ONLY: when BOTH are set, the OTP for dev_fixed_otp_email is always dev_fixed_otp (6 digits).
    # Every other email still gets a random code. Never set in production.
    dev_fixed_otp: str = ""
    dev_fixed_otp_email: str = ""

    @property
    def fixed_otp_email(self) -> str:
        """The test account that uses the fixed code, or "" when test mode is off."""
        email = self.dev_fixed_otp_email.strip().lower()
        return email if self.dev_fixed_otp and email else ""

    # --- Inbound API gateway
    gateway_default_rate_per_min: int = 60

    # --- Outbound gateway policies (per upstream)
    outbound_max_retries: int = 2
    outbound_backoff_seconds: float = 0.5
    outbound_breaker_threshold: int = 5
    outbound_breaker_cooldown_seconds: float = 30.0
    outbound_rate_per_sec: float = 20.0

    # --- AI (Claude) via outbound gateway
    ai_provider: Literal["disabled", "anthropic", "bedrock"] = "disabled"
    ai_model: str = ""                          # default: claude-opus-5-5 / anthropic.claude-opus-5-5
    ai_bedrock_fallback_model: str = "anthropic.claude-opus-4-8"
    ai_timeout: float = 120.0
    ai_rate_per_sec: float = 2.0


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.dev_fixed_otp and not (len(s.dev_fixed_otp) == 6 and s.dev_fixed_otp.isdigit()):
        raise ValueError("CORPONE_DEV_FIXED_OTP must be exactly 6 digits")
    return s
