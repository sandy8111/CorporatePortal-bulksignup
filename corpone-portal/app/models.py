"""Database schema for the CorpOne corporate bulk-line portal."""
import enum
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean, Date, DateTime, Enum, ForeignKey, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class SimType(str, enum.Enum):
    ESIM = "ESIM"
    PHYSICAL = "PHYSICAL"


class InventoryStatus(str, enum.Enum):
    AVAILABLE = "AVAILABLE"  # uploaded, not yet paired
    PAIRED = "PAIRED"        # SIM <-> number paired, free to allocate
    RESERVED = "RESERVED"    # held in a sign-up cart
    ASSIGNED = "ASSIGNED"    # attached to a live line


class SubjectType(str, enum.Enum):
    COMPANY = "COMPANY"
    ACCOUNT_OFFICER = "ACCOUNT_OFFICER"


class IdType(str, enum.Enum):
    UEN = "UEN"
    NRIC = "NRIC"
    FIN = "FIN"
    PASSPORT = "PASSPORT"


class SignupStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    COMPLETED = "COMPLETED"
    PARTIALLY_FAILED = "PARTIALLY_FAILED"
    FAILED = "FAILED"


class LineStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROVISIONED = "PROVISIONED"
    FAILED = "FAILED"


# --------------------------------------------------------------------------- reference data

class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    monthly_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    data_gb: Mapped[int]
    description: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Merchant(Base):
    """Dealer / channel whose merchantCode is delegated to account officers for sign-ups."""
    __tablename__ = "merchants"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class BlacklistEntry(Base):
    __tablename__ = "blacklist_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    subject_type: Mapped[SubjectType] = mapped_column(Enum(SubjectType))
    id_type: Mapped[IdType] = mapped_column(Enum(IdType))
    id_value: Mapped[str] = mapped_column(String(40), index=True)  # stored upper-cased
    reason: Mapped[str] = mapped_column(Text, default="")
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # None = indefinite
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# --------------------------------------------------------------------------- inventory

class Sim(Base):
    __tablename__ = "sim_inventory"
    id: Mapped[int] = mapped_column(primary_key=True)
    iccid: Mapped[str] = mapped_column(String(20), unique=True)
    sim_type: Mapped[SimType] = mapped_column(Enum(SimType))
    category: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[InventoryStatus] = mapped_column(
        Enum(InventoryStatus), default=InventoryStatus.AVAILABLE, index=True)
    # eSIM activation code parts (SGP.22: LPA:1$<smdp_address>$<matching_id>)
    smdp_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    matching_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    number_id: Mapped[int | None] = mapped_column(
        ForeignKey("number_inventory.id"), unique=True, nullable=True)
    paired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    number: Mapped["MobileNumber | None"] = relationship(back_populates="sim")


class MobileNumber(Base):
    __tablename__ = "number_inventory"
    id: Mapped[int] = mapped_column(primary_key=True)
    msisdn: Mapped[str] = mapped_column(String(15), unique=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[InventoryStatus] = mapped_column(
        Enum(InventoryStatus), default=InventoryStatus.AVAILABLE, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    sim: Mapped["Sim | None"] = relationship(back_populates="number")


# --------------------------------------------------------------------------- sign-up (draft order)

class Signup(Base):
    """A draft bulk-line order. Turns into Company/AO/Account/Lines on submit."""
    __tablename__ = "signups"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    status: Mapped[SignupStatus] = mapped_column(Enum(SignupStatus), default=SignupStatus.DRAFT)

    uen: Mapped[str] = mapped_column(String(10))
    company_name: Mapped[str] = mapped_column(String(200))
    company_address: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(40))
    contract_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    contract_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)

    ao_full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ao_id_type: Mapped[IdType | None] = mapped_column(Enum(IdType), nullable=True)
    ao_id_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ao_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    ao_mobile: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ao_designation: Mapped[str | None] = mapped_column(String(100), nullable=True)
    merchant_code: Mapped[str | None] = mapped_column(String(40), nullable=True)

    owner_email: Mapped[str] = mapped_column(String(254), index=True)  # verified AO login
    contract_ai_review: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON, advisory
    account_id: Mapped[int | None] = mapped_column(ForeignKey("corporate_accounts.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    cart_lines: Mapped[list["CartLine"]] = relationship(
        back_populates="signup", cascade="all, delete-orphan")


class CartLine(Base):
    __tablename__ = "cart_lines"
    __table_args__ = (UniqueConstraint("sim_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    signup_id: Mapped[str] = mapped_column(ForeignKey("signups.id"), index=True)
    sim_id: Mapped[int] = mapped_column(ForeignKey("sim_inventory.id"))
    number_id: Mapped[int] = mapped_column(ForeignKey("number_inventory.id"))
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"))

    signup: Mapped[Signup] = relationship(back_populates="cart_lines")
    sim: Mapped[Sim] = relationship()
    number: Mapped[MobileNumber] = relationship()
    plan: Mapped[Plan] = relationship()


# --------------------------------------------------------------------------- customer records (created on submit)

class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(primary_key=True)
    uen: Mapped[str] = mapped_column(String(10), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(Text, default="")
    contract_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AccountOfficer(Base):
    __tablename__ = "account_officers"
    __table_args__ = (UniqueConstraint("company_id", "id_type", "id_number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    full_name: Mapped[str] = mapped_column(String(200))
    id_type: Mapped[IdType] = mapped_column(Enum(IdType))
    id_number: Mapped[str] = mapped_column(String(40))
    email: Mapped[str] = mapped_column(String(254))
    mobile: Mapped[str] = mapped_column(String(20))
    designation: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CorporateAccount(Base):
    __tablename__ = "corporate_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_number: Mapped[str] = mapped_column(String(20), unique=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    account_officer_id: Mapped[int] = mapped_column(ForeignKey("account_officers.id"))
    merchant_code: Mapped[str] = mapped_column(String(40))
    billing_account_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    company: Mapped[Company] = relationship()
    account_officer: Mapped[AccountOfficer] = relationship()
    lines: Mapped[list["Line"]] = relationship(back_populates="account")


class Line(Base):
    __tablename__ = "lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("corporate_accounts.id"), index=True)
    sim_id: Mapped[int] = mapped_column(ForeignKey("sim_inventory.id"), unique=True)
    number_id: Mapped[int] = mapped_column(ForeignKey("number_inventory.id"), unique=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"))
    status: Mapped[LineStatus] = mapped_column(Enum(LineStatus), default=LineStatus.PENDING)
    network_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    billing_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    esim_qr_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    account: Mapped[CorporateAccount] = relationship(back_populates="lines")
    sim: Mapped[Sim] = relationship()
    number: Mapped[MobileNumber] = relationship()
    plan: Mapped[Plan] = relationship()


# --------------------------------------------------------------------------- AO login (email OTP)

class OtpChallenge(Base):
    __tablename__ = "otp_challenges"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class AoSession(Base):
    __tablename__ = "ao_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(254), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# --------------------------------------------------------------------------- API gateway

class ApiClient(Base):
    """A system or AI agent allowed to call /gateway/v1 with an API key."""
    __tablename__ = "api_clients"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    key_prefix: Mapped[str] = mapped_column(String(16), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    scopes: Mapped[str] = mapped_column(Text)  # space-separated
    rate_limit_per_min: Mapped[int]
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GatewayAuditLog(Base):
    __tablename__ = "gateway_audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    method: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(500))
    status_code: Mapped[int]
    latency_ms: Mapped[int]
    remote_ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class OutboundCallLog(Base):
    __tablename__ = "outbound_call_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    upstream: Mapped[str] = mapped_column(String(40), index=True)
    operation: Mapped[str] = mapped_column(String(80))
    attempt: Mapped[int]
    outcome: Mapped[str] = mapped_column(String(20))  # ok | error | retry | circuit_open
    latency_ms: Mapped[int]
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
