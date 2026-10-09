"""Build Arrow schemas from domain models, so Parquet columns always match the models."""

from datetime import date, datetime
from enum import StrEnum
from types import NoneType, UnionType
from typing import Annotated, Union, get_args, get_origin

import pyarrow as pa
from pydantic import AwareDatetime

from sentinel.domain.base import DomainModel

UTC_TIMESTAMP = pa.timestamp("us", tz="UTC")

# Checked in order: bool before int (bool is an int), datetime before date
# (datetime is a date), StrEnum before str.
ARROW_TYPES: tuple[tuple[type, pa.DataType], ...] = (
    (bool, pa.bool_()),
    (int, pa.int64()),
    (float, pa.float64()),
    (StrEnum, pa.string()),
    (str, pa.string()),
    (AwareDatetime, UTC_TIMESTAMP),
    (datetime, UTC_TIMESTAMP),
    (date, pa.date32()),
)


def schema_for(model: type[DomainModel]) -> pa.Schema:
    """Return the Arrow schema for a model: one column per field, in field order."""
    return pa.schema(
        [
            pa.field(name, *_arrow_type(field.annotation, name))
            for name, field in model.model_fields.items()
        ]
    )


def _arrow_type(annotation: object, field_name: str) -> tuple[pa.DataType, bool]:
    """Return the Arrow type for a field annotation, and whether it can be null."""
    base, is_nullable = _strip_optional(annotation)
    if get_origin(base) is Annotated:
        base = get_args(base)[0]  # drop Pydantic constraints such as patterns

    for python_type, arrow_type in ARROW_TYPES:
        if isinstance(base, type) and issubclass(base, python_type):
            return arrow_type, is_nullable
    raise TypeError(f"no Arrow type for field {field_name!r} of type {annotation!r}")


def _strip_optional(annotation: object) -> tuple[object, bool]:
    """Turn ``X | None`` into ``(X, True)``; anything else into ``(annotation, False)``."""
    if get_origin(annotation) not in (Union, UnionType):
        return annotation, False

    members = [arg for arg in get_args(annotation) if arg is not NoneType]
    if len(members) != 1:
        raise TypeError(f"only 'X | None' unions are supported, got {annotation!r}")
    return members[0], True
