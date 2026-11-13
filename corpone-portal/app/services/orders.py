"""Order submission and provisioning into the network and billing systems."""
import logging
import secrets
from datetime import datetime, timezone
from html import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AccountOfficer, Company, CorporateAccount, IdType, InventoryStatus, Line, LineStatus,
    Merchant, Signup, SignupStatus, SimType, SubjectType,
)
from app.services import email as email_svc
from app.services import esim
from app.services.blacklist import active_entry
from app.services.telco_api import TelcoApiError, get_billing_api, get_network_api
from app.validators import ValidationError

log = logging.getLogger(__name__)
QR_PER_EMAIL = 50


def check_not_blacklisted(db: Session, signup: Signup) -> None:
    if active_entry(db, SubjectType.COMPANY, IdType.UEN, signup.uen):
        raise ValidationError(f"Company {signup.uen} is blacklisted")
    if signup.ao_id_number and active_entry(
            db, SubjectType.ACCOUNT_OFFICER, signup.ao_id_type, signup.ao_id_number):
        raise ValidationError("Account officer is blacklisted")


def active_merchant(db: Session, code: str) -> Merchant | None:
    return db.scalars(select(Merchant).where(Merchant.code == code.strip().upper(),
                                             Merchant.active.is_(True))).first()


def submit(db: Session, signup: Signup) -> CorporateAccount:
    """Validate the cart and create Company, Account Officer, Account and Lines atomically."""
    if signup.status is not SignupStatus.DRAFT:
        raise ValidationError("Sign-up has already been submitted")
    if not signup.contract_key:
        raise ValidationError("Contract agreement has not been uploaded")
    if not signup.ao_id_number:
        raise ValidationError("Account officer details are missing")
    if not signup.merchant_code or not active_merchant(db, signup.merchant_code):
        raise ValidationError("Merchant code is not valid or not active")
    if not signup.cart_lines:
        raise ValidationError("Cart is empty")
    check_not_blacklisted(db, signup)

    company = db.scalars(select(Company).where(Company.uen == signup.uen)).first()
    if company is None:
        company = Company(uen=signup.uen, name=signup.company_name, address=signup.company_address)
        db.add(company)
    company.contract_key = signup.contract_key
    db.flush()

    ao = db.scalars(select(AccountOfficer).where(
        AccountOfficer.company_id == company.id, AccountOfficer.id_type == signup.ao_id_type,
        AccountOfficer.id_number == signup.ao_id_number)).first()
    if ao is None:
        ao = AccountOfficer(company_id=company.id, id_type=signup.ao_id_type, id_number=signup.ao_id_number)
        db.add(ao)
    ao.full_name = signup.ao_full_name
    ao.email = signup.ao_email
    ao.mobile = signup.ao_mobile
    ao.designation = signup.ao_designation or ""
    db.flush()

    account = CorporateAccount(
        account_number=f"CA{secrets.randbelow(10**10):010d}",
        company_id=company.id, account_officer_id=ao.id, merchant_code=signup.merchant_code,
    )
    db.add(account)
    db.flush()

    for cl in signup.cart_lines:
        if cl.sim.status is not InventoryStatus.RESERVED or cl.number.status is not InventoryStatus.RESERVED:
            raise ValidationError(f"SIM {cl.sim.iccid} is no longer reserved for this cart")
        cl.sim.status = InventoryStatus.ASSIGNED
        cl.number.status = InventoryStatus.ASSIGNED
        db.add(Line(account_id=account.id, sim_id=cl.sim_id, number_id=cl.number_id, plan_id=cl.plan_id))

    signup.status = SignupStatus.SUBMITTED
    signup.submitted_at = datetime.now(timezone.utc)
    signup.account_id = account.id
    db.commit()
    return account


def provision(db: Session, account_id: int) -> None:
    """Push the account and its pending/failed lines to billing and network. Safe to re-run."""
    account = db.get(CorporateAccount, account_id)
    network, billing = get_network_api(), get_billing_api()
    company, ao = account.company, account.account_officer

    if not account.billing_account_ref:
        try:
            account.billing_account_ref = billing.create_account(
                account_number=account.account_number,
                company={"uen": company.uen, "name": company.name, "address": company.address},
                account_officer={"name": ao.full_name, "idType": ao.id_type.value,
                                 "idNumber": ao.id_number, "email": ao.email, "mobile": ao.mobile},
                merchant_code=account.merchant_code,
                idempotency_key=f"account-{account.id}",
            )
            db.commit()
        except TelcoApiError as exc:
            log.error("Billing account creation failed for %s: %s", account.account_number, exc)
            for line in account.lines:
                if line.status is not LineStatus.PROVISIONED:
                    line.status, line.last_error = LineStatus.FAILED, str(exc)
            _finish(db, account)
            return

    for line in account.lines:
        if line.status is LineStatus.PROVISIONED:
            continue
        try:
            if not line.network_ref:
                line.network_ref = network.activate_line(
                    iccid=line.sim.iccid, msisdn=line.number.msisdn, sim_type=line.sim.sim_type.value,
                    plan_code=line.plan.code, idempotency_key=f"line-{line.id}-network")
            if not line.billing_ref:
                line.billing_ref = billing.add_subscription(
                    billing_account_ref=account.billing_account_ref, msisdn=line.number.msisdn,
                    iccid=line.sim.iccid, plan_code=line.plan.code,
                    monthly_price=str(line.plan.monthly_price), idempotency_key=f"line-{line.id}-billing")
            line.status, line.last_error = LineStatus.PROVISIONED, None
        except TelcoApiError as exc:
            line.status, line.last_error = LineStatus.FAILED, str(exc)
        db.commit()

    send_esim_qr_codes(db, account)
    _finish(db, account)


def send_esim_qr_codes(db: Session, account: CorporateAccount) -> int:
    """Email QR codes for provisioned eSIM lines that have not been sent yet. Returns count sent."""
    pending = [l for l in account.lines if l.status is LineStatus.PROVISIONED
               and l.sim.sim_type is SimType.ESIM and l.esim_qr_sent_at is None]
    sent = 0
    ao = account.account_officer
    for start in range(0, len(pending), QR_PER_EMAIL):
        batch = pending[start:start + QR_PER_EMAIL]
        rows, attachments = [], []
        for line in batch:
            code = esim.activation_code(line.sim)
            attachments.append((f"eSIM-{line.number.msisdn}.png", esim.qr_png(code), "image/png"))
            rows.append((line.number.msisdn, line.sim.iccid, line.plan.name, code))
        part = f" (part {start // QR_PER_EMAIL + 1})" if len(pending) > QR_PER_EMAIL else ""
        text = (f"Dear {ao.full_name},\n\nYour CorpOne corporate eSIM lines for {account.company.name} "
                f"(account {account.account_number}) are ready. Scan the attached QR code for each "
                "line on the target device.\n\n" +
                "\n".join(f"{m}  ICCID {i}  {p}  {c}" for m, i, p, c in rows) +
                "\n\nEach QR code can be installed only once. Keep these codes confidential.\n\nCorpOne")
        html = (f"<p>Dear {escape(ao.full_name)},</p><p>Your CorpOne corporate eSIM lines for "
                f"<b>{escape(account.company.name)}</b> (account {account.account_number}) are ready. "
                "Scan the attached QR code for each line on the target device.</p><table border=1 "
                "cellpadding=4><tr><th>Number</th><th>ICCID</th><th>Plan</th><th>Manual code</th></tr>" +
                "".join(f"<tr><td>{m}</td><td>{i}</td><td>{escape(p)}</td><td><code>{escape(c)}</code></td></tr>"
                        for m, i, p, c in rows) +
                "</table><p>Each QR code can be installed only once. Keep these codes confidential.</p>")
        try:
            email_svc.send(email_svc.build_message(
                ao.email, f"CorpOne eSIM activation - {account.account_number}{part}", text, html, attachments))
        except Exception:
            log.exception("Failed to send eSIM QR email for account %s", account.account_number)
            break
        now = datetime.now(timezone.utc)
        for line in batch:
            line.esim_qr_sent_at = now
        db.commit()
        sent += len(batch)
    return sent


def account_view(db: Session, a: CorporateAccount) -> dict:
    signup = db.scalars(select(Signup).where(Signup.account_id == a.id)).first()
    return {
        "account_number": a.account_number, "billing_account_ref": a.billing_account_ref,
        "order_status": signup.status.value if signup else None,
        "company": {"uen": a.company.uen, "name": a.company.name},
        "account_officer": {"name": a.account_officer.full_name, "email": a.account_officer.email},
        "merchant_code": a.merchant_code,
        "lines": [{"msisdn": l.number.msisdn, "iccid": l.sim.iccid, "sim_type": l.sim.sim_type.value,
                   "plan": l.plan.name, "status": l.status.value, "network_ref": l.network_ref,
                   "billing_ref": l.billing_ref, "error": l.last_error,
                   "esim_qr_sent": l.esim_qr_sent_at is not None} for l in a.lines],
    }


def find_account(db: Session, account_number: str) -> CorporateAccount | None:
    return db.scalars(select(CorporateAccount).where(CorporateAccount.account_number == account_number)).first()


def _finish(db: Session, account: CorporateAccount) -> None:
    statuses = {l.status for l in account.lines}
    signup = db.scalars(select(Signup).where(Signup.account_id == account.id)).first()
    if signup:
        if statuses == {LineStatus.PROVISIONED}:
            signup.status = SignupStatus.COMPLETED
        elif LineStatus.PROVISIONED in statuses:
            signup.status = SignupStatus.PARTIALLY_FAILED
        elif LineStatus.PENDING not in statuses:
            signup.status = SignupStatus.FAILED
    db.commit()
