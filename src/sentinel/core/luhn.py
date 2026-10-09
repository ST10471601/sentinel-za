"""Luhn (mod 10) check digits, used by SA ID numbers and card numbers."""


def luhn_check_digit(payload: str) -> str:
    """Return the digit that makes ``payload`` + digit pass the Luhn check."""
    if not _is_ascii_digits(payload):
        raise ValueError(f"payload must contain only digits: {payload!r}")

    total = 0
    # Walk right to left. The check digit will sit to the right, so the
    # rightmost payload digit is the first one to double.
    for position, char in enumerate(reversed(payload)):
        digit = int(char)
        if position % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return str((10 - total % 10) % 10)


def is_luhn_valid(number: str) -> bool:
    """Return True if the last digit of ``number`` is its correct Luhn check digit."""
    if len(number) < 2 or not _is_ascii_digits(number):
        return False
    return luhn_check_digit(number[:-1]) == number[-1]


def _is_ascii_digits(value: str) -> bool:
    # str.isdigit() alone also accepts characters like "²".
    return value.isascii() and value.isdigit()
