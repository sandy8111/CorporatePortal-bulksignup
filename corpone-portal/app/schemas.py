from datetime import date

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.models import IdType, SimType, SubjectType
from app import validators as v


class SignupCreate(BaseModel):
    uen: str
    company_name: str = Field(default="", max_length=200)   # required only for a new company
    company_address: str = Field(default="", max_length=500)

    @field_validator("uen")
    @classmethod
    def _uen(cls, x): return v.validate_uen(x)


class CategoryChoice(BaseModel):
    category: str

    @field_validator("category")
    @classmethod
    def _cat(cls, x): return v.validate_category(x)


class CategoryAllocationIn(BaseModel):
    category: str
    uen: str | None = None  # blank/None = available to all companies
    description: str = Field(default="", max_length=200)
    active: bool = True

    @field_validator("category")
    @classmethod
    def _cat(cls, x): return v.validate_category(x)

    @field_validator("uen")
    @classmethod
    def _uen(cls, x): return v.validate_uen(x) if x and x.strip() else None


class AccountOfficerIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    id_type: IdType
    id_number: str
    email: EmailStr | None = None  # always the verified login email; optional
    mobile: str
    designation: str = Field(default="", max_length=100)
    merchant_code: str = Field(min_length=1, max_length=40)

    @field_validator("mobile")
    @classmethod
    def _mobile(cls, x): return v.validate_msisdn(x)

    @model_validator(mode="after")
    def _id(self):
        self.id_number = v.validate_person_id(self.id_type, self.id_number)
        self.merchant_code = self.merchant_code.strip().upper()
        return self


class CartAdd(BaseModel):
    plan_code: str
    sim_type: SimType
    quantity: int = Field(ge=1, le=5000)


class BlacklistIn(BaseModel):
    subject_type: SubjectType
    id_type: IdType
    id_value: str
    reason: str = Field(default="", max_length=1000)
    start_date: date
    end_date: date | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.subject_type is SubjectType.COMPANY:
            if self.id_type is not IdType.UEN:
                raise ValueError("Company blacklist entries must use id_type UEN")
            self.id_value = v.validate_uen(self.id_value)
        else:
            self.id_value = v.validate_person_id(self.id_type, self.id_value)
        if self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class MerchantIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    active: bool = True
