"""Money helpers. Amounts are always stored as integer ZAR cents."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sentinel.core.errors import DataValidationError

CENTS_PER_RAND = 100


def to_cents(rands: Decimal | int | str) -> int:
    """Convert a rand amount to cents, rounding half up."""
    # Floats can't hold most decimals exactly, so refuse them outright.
    if isinstance(rands, bool) or not isinstance(rands, Decimal | int | str):
        raise TypeError(f"rand amount must be Decimal, int or str, got {type(rands).__name__}")

    try:
        amount = Decimal(rands)
    except InvalidOperation as exc:
        raise DataValidationError(f"invalid rand amount: {rands!r}") from exc

    if not amount.is_finite():  # NaN or Infinity
        raise DataValidationError(f"rand amount must be finite: {rands!r}")

    return int((amount * CENTS_PER_RAND).to_integral_value(rounding=ROUND_HALF_UP))


def to_rands(cents: int) -> Decimal:
    """Convert cents to rands with two decimal places."""
    return (Decimal(cents) / CENTS_PER_RAND).quantize(Decimal("0.01"))


def format_zar(cents: int) -> str:
    """Format cents for display, e.g. 123456 -> 'R1,234.56'."""
    sign = "-" if cents < 0 else ""
    return f"{sign}R{to_rands(abs(cents)):,.2f}"
