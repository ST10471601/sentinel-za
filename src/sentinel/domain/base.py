"""Base model and field types shared by all domain entities."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

# Money is always integer ZAR cents, never floats.
Cents = Annotated[int, Field(ge=0)]
PositiveCents = Annotated[int, Field(gt=0)]

Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]

CountryCode = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]  # ISO 3166 alpha-2


class DomainModel(BaseModel):
    """Base for domain entities: immutable, strictly typed and no unknown fields.

    Strict mode stops silent conversions such as "100" becoming 100.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
