import re

import pyarrow as pa
import pytest

from sentinel.domain.accounts import Account
from sentinel.domain.base import DomainModel
from sentinel.domain.customers import Customer
from sentinel.domain.merchants import Merchant
from sentinel.simulator.arrow_schema import UTC_TIMESTAMP, schema_for


@pytest.mark.parametrize("model", [Customer, Account, Merchant])
def test_schema_has_one_column_per_field_in_order(model: type[DomainModel]) -> None:
    assert schema_for(model).names == list(model.model_fields)


@pytest.mark.parametrize(
    ("model", "column", "expected_type", "is_nullable"),
    [
        (Customer, "customer_id", pa.string(), False),
        (Customer, "customer_type", pa.string(), False),  # enum
        (Customer, "sa_id_number", pa.string(), True),  # constrained and optional
        (Customer, "date_of_birth", pa.date32(), True),
        (Customer, "pay_day", pa.int64(), False),
        (Customer, "home_lat", pa.float64(), False),
        (Customer, "onboarded_at", UTC_TIMESTAMP, False),
        (Account, "credit_limit_cents", pa.int64(), True),
        (Merchant, "is_online", pa.bool_(), False),
        (Merchant, "lat", pa.float64(), True),
    ],
)
def test_field_types_map_to_arrow(
    model: type[DomainModel],
    column: str,
    expected_type: pa.DataType,
    is_nullable: bool,
) -> None:
    field = schema_for(model).field(column)
    assert field.type == expected_type
    assert field.nullable is is_nullable


class ListModel(DomainModel):
    values: list[int]


class MixedUnionModel(DomainModel):
    value: int | str | None


def test_unsupported_field_types_are_rejected() -> None:
    with pytest.raises(TypeError, match="no Arrow type for field 'values'"):
        schema_for(ListModel)


def test_unions_other_than_optional_are_rejected() -> None:
    with pytest.raises(TypeError, match=re.escape("only 'X | None' unions")):
        schema_for(MixedUnionModel)
