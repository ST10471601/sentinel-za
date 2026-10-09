"""Bank customers: individuals and businesses."""

from datetime import date
from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from sentinel.core.datetimes import UtcDatetime
from sentinel.core.luhn import is_luhn_valid
from sentinel.domain.base import DomainModel, Latitude, Longitude

CustomerId = Annotated[str, Field(pattern=r"^CUS-\d{7}$")]

SaIdNumber = Annotated[str, Field(pattern=r"^\d{13}$")]
CompanyRegNumber = Annotated[str, Field(pattern=r"^\d{4}/\d{6}/\d{2}$")]  # CIPC format
SaMobileNumber = Annotated[str, Field(pattern=r"^\+27[6-8]\d{8}$")]
EmailAddress = Annotated[str, Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]


class CustomerType(StrEnum):
    """Whether the customer is a person or a registered business."""

    INDIVIDUAL = "individual"
    BUSINESS = "business"


class IncomeBand(StrEnum):
    """Income level. Drives spending behaviour."""

    LOW = "low"
    MIDDLE = "middle"
    HIGH = "high"
    BUSINESS = "business"


class IncomeSource(StrEnum):
    """Main source of income. Drives payday timing."""

    SALARY = "salary"
    GRANT = "grant"
    BUSINESS = "business"
    MIXED = "mixed"


class Province(StrEnum):
    """The nine provinces of South Africa."""

    EASTERN_CAPE = "Eastern Cape"
    FREE_STATE = "Free State"
    GAUTENG = "Gauteng"
    KWAZULU_NATAL = "KwaZulu-Natal"
    LIMPOPO = "Limpopo"
    MPUMALANGA = "Mpumalanga"
    NORTH_WEST = "North West"
    NORTHERN_CAPE = "Northern Cape"
    WESTERN_CAPE = "Western Cape"


class BankingChannel(StrEnum):
    """Digital channel a customer uses to bank."""

    APP = "app"
    INTERNET = "internet"
    USSD = "ussd"


class Customer(DomainModel):
    """One bank customer.

    Individuals have an SA ID number and date of birth; businesses have a company
    registration number instead.
    """

    customer_id: CustomerId
    customer_type: CustomerType
    full_name: str = Field(min_length=1)
    sa_id_number: SaIdNumber | None
    company_reg_number: CompanyRegNumber | None
    date_of_birth: date | None
    phone_number: SaMobileNumber
    email: EmailAddress
    income_band: IncomeBand
    income_source: IncomeSource
    pay_day: int = Field(ge=1, le=31)
    home_province: Province
    home_city: str = Field(min_length=1)
    home_lat: Latitude
    home_lon: Longitude
    preferred_channel: BankingChannel
    onboarded_at: UtcDatetime

    @model_validator(mode="after")
    def check_identity_fields(self) -> Self:
        """Individuals need ID number and birth date; businesses need a registration number."""
        has_id_number = self.sa_id_number is not None
        has_birth_date = self.date_of_birth is not None
        has_reg_number = self.company_reg_number is not None

        if self.customer_type is CustomerType.INDIVIDUAL:
            if not (has_id_number and has_birth_date) or has_reg_number:
                raise ValueError("individuals need sa_id_number and date_of_birth only")
        elif has_id_number or has_birth_date or not has_reg_number:
            raise ValueError("businesses need company_reg_number only")
        return self

    @model_validator(mode="after")
    def check_sa_id_number(self) -> Self:
        """The ID number must start with the birth date and end with a valid check digit."""
        if self.sa_id_number is None or self.date_of_birth is None:
            return self
        if not self.sa_id_number.startswith(f"{self.date_of_birth:%y%m%d}"):
            raise ValueError("sa_id_number does not match date_of_birth")
        if not is_luhn_valid(self.sa_id_number):
            raise ValueError("sa_id_number has an invalid check digit")
        return self

    @model_validator(mode="after")
    def check_business_income_band(self) -> Self:
        """Businesses, and only businesses, use the business income band."""
        is_business = self.customer_type is CustomerType.BUSINESS
        if is_business != (self.income_band is IncomeBand.BUSINESS):
            raise ValueError("income_band 'business' is only for business customers")
        return self
