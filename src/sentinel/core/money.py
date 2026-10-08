"""Money helpers. Every amount in the system is stored as integer ZAR cents.

Floats cannot represent most decimal fractions exactly (``0.1 + 0.2 != 0.3``),
so they are rejected at the boundary. Convert rand values with :func:`to_cents`
and display amounts with :func:`format_zar`.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sentinel.core.errors import DataValidationError

CENTS_PER_RAND = 100
_ONE_CENT = Decimal("0.01")


def to_cents(rands: Decimal | int | str) -> int:
    """Convert a rand amount to integer cents, rounding half up.

    Args:
        rands: Amount in rand, e.g. ``"1234.56"``, ``Decimal("10")`` or ``50``.

    Returns:
        The amount in cents, e.g. ``123456``.

    Raises:
        TypeError: If given a float or bool (floats lose precision).
        DataValidationError: If the value is not a finite number.
    """
    if isinstance(rands, bool) or not isinstance(rands, Decimal | int | str):
        msg = f"Rand amounts must be Decimal, int or str, not {type(rands).__name__}"
        raise TypeError(msg)
    try:
        amount = Decimal(rands)
    except InvalidOperation as exc:
        msg = f"Not a valid rand amount: {rands!r}"
        raise DataValidationError(msg) from exc
    if not amount.is_finite():
        msg = f"Rand amount must be finite: {rands!r}"
        raise DataValidationError(msg)
    return int((amount * CENTS_PER_RAND).to_integral_value(rounding=ROUND_HALF_UP))


def to_rands(cents: int) -> Decimal:
    """Convert integer cents to a rand ``Decimal`` with two decimal places."""
    return (Decimal(cents) / CENTS_PER_RAND).quantize(_ONE_CENT)


def format_zar(cents: int) -> str:
    """Format cents for display, e.g. ``123456`` becomes ``"R1,234.56"``."""
    sign = "-" if cents < 0 else ""
    return f"{sign}R{to_rands(abs(cents)):,.2f}"
