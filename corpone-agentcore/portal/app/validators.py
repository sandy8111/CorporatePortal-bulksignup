"""Field-level validation rules for the CorpOne portal."""
import re

from stdnum.sg import uen as _uen

from app.models import IdType

ICCID_RE = re.compile(r"^\d{18,20}$")
# Singapore mobile numbers are 8 digits and start with 8 or 9.
MSISDN_RE = re.compile(r"^[89]\d{7}$")
CATEGORY_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,39}$")
NRIC_FIN_RE = re.compile(r"^[STFGM]\d{7}[A-Z]$")
PASSPORT_RE = re.compile(r"^[A-Z0-9]{5,20}$")


class ValidationError(ValueError):
    pass


def normalize_uen(value: str) -> str:
    return re.sub(r"\s", "", value or "").upper()


def validate_uen(value: str) -> str:
    """Validate a Singapore UEN (ACRA business / local company / other-entity formats + check letter)."""
    v = normalize_uen(value)
    try:
        return _uen.validate(v)
    except Exception as exc:  # stdnum raises several ValidationError subclasses
        raise ValidationError(f"'{value}' is not a valid Singapore UEN") from exc


def validate_iccid(value: str) -> str:
    v = (value or "").strip()
    if not ICCID_RE.fullmatch(v):
        raise ValidationError("SIM ICCID must be numeric and 18 to 20 digits long")
    return v


def validate_msisdn(value: str) -> str:
    v = re.sub(r"[\s-]", "", value or "")
    if v.startswith("+65"):
        v = v[3:]
    elif v.startswith("65") and len(v) == 10:
        v = v[2:]
    if not MSISDN_RE.fullmatch(v):
        raise ValidationError("Number must be 8 digits and start with 8 or 9")
    return v


def validate_category(value: str) -> str:
    v = (value or "").strip().upper()
    if not CATEGORY_RE.fullmatch(v):
        raise ValidationError("Category must be 1-40 chars: letters, digits, '-' or '_'")
    return v


_ST_LETTERS = "JZIHGFEDCBA"
_FG_LETTERS = "XWUTRQPNMLK"
_WEIGHTS = (2, 7, 6, 5, 4, 3, 2)


def _nric_check_ok(v: str) -> bool:
    prefix, digits, check = v[0], v[1:8], v[8]
    if prefix == "M":
        # M-series FINs (2022+) use a different scheme; accept on format only.
        return True
    total = sum(int(d) * w for d, w in zip(digits, _WEIGHTS))
    if prefix in "TG":
        total += 4
    table = _ST_LETTERS if prefix in "ST" else _FG_LETTERS
    return table[total % 11] == check


def validate_person_id(id_type: IdType, value: str) -> str:
    v = re.sub(r"\s", "", value or "").upper()
    if id_type in (IdType.NRIC, IdType.FIN):
        if not NRIC_FIN_RE.fullmatch(v):
            raise ValidationError("NRIC/FIN must look like S1234567D")
        if id_type is IdType.NRIC and v[0] not in "ST":
            raise ValidationError("NRIC must start with S or T")
        if id_type is IdType.FIN and v[0] not in "FGM":
            raise ValidationError("FIN must start with F, G or M")
        if not _nric_check_ok(v):
            raise ValidationError("NRIC/FIN check letter is invalid")
        return v
    if id_type is IdType.PASSPORT:
        if not PASSPORT_RE.fullmatch(v):
            raise ValidationError("Passport number must be 5-20 letters/digits")
        return v
    raise ValidationError("Account officer ID type must be NRIC, FIN or PASSPORT")
