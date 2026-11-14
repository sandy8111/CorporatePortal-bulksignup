import pytest

from app.models import IdType
from app.validators import (
    ValidationError, validate_iccid, validate_msisdn, validate_person_id, validate_uen,
)


@pytest.mark.parametrize("uen", ["00192200M", "197401143C", "S16FC0121D", "T01FC6132D", " 197401143c "])
def test_valid_uen(uen):
    assert validate_uen(uen) == uen.strip().upper()


@pytest.mark.parametrize("uen", ["", "12345678X", "201912345A", "197401143D", "ABCDEFGHIJ"])
def test_invalid_uen(uen):
    with pytest.raises(ValidationError):
        validate_uen(uen)


@pytest.mark.parametrize("iccid", ["8965" + "0" * 14, "8965" + "1" * 15, "8965" + "2" * 16])
def test_valid_iccid(iccid):
    assert validate_iccid(iccid) == iccid


@pytest.mark.parametrize("iccid", ["8965" + "0" * 13, "8965" + "0" * 17, "89650000000000000A", ""])
def test_invalid_iccid(iccid):
    with pytest.raises(ValidationError):
        validate_iccid(iccid)


@pytest.mark.parametrize("raw,expected", [("81234567", "81234567"), ("9123 4567", "91234567"),
                                          ("+6591234567", "91234567"), ("6581234567", "81234567")])
def test_valid_msisdn(raw, expected):
    assert validate_msisdn(raw) == expected


@pytest.mark.parametrize("raw", ["61234567", "71234567", "9123456", "912345678", "9123456a"])
def test_invalid_msisdn(raw):
    with pytest.raises(ValidationError):
        validate_msisdn(raw)


def test_nric_checksum():
    assert validate_person_id(IdType.NRIC, "s1234567d") == "S1234567D"
    with pytest.raises(ValidationError):
        validate_person_id(IdType.NRIC, "S1234567A")
    with pytest.raises(ValidationError):
        validate_person_id(IdType.NRIC, "F1234567N")  # FIN prefix under NRIC type
    assert validate_person_id(IdType.PASSPORT, "e1234567x") == "E1234567X"
