"""Synthetic identifiers: entity IDs, SA ID numbers, phone, account and card references.

Values look real but are generated. Functions that need randomness take the caller's
``random.Random`` so output is reproducible from the simulation seed. That generator is
not cryptographically secure, which is fine for synthetic data.
"""

import random
from datetime import date

from sentinel.core.luhn import luhn_check_digit

ID_DIGITS = 7

# First two digits after +27 used by SA mobile networks.
MOBILE_PREFIXES: tuple[str, ...] = (
    "60", "61", "62", "63", "64", "65", "66", "67", "68",
    "71", "72", "73", "74", "76", "78", "79",
    "81", "82", "83", "84",
)  # fmt: skip

PRIVATE_COMPANY_SUFFIX = "07"  # CIPC entity type for a (Pty) Ltd


def make_id(prefix: str, number: int, digits: int = ID_DIGITS) -> str:
    """Build an entity ID such as ``CUS-0000042``. High-volume events use more digits."""
    max_number = 10**digits - 1
    if not 1 <= number <= max_number:
        raise ValueError(f"ID number must be between 1 and {max_number}, got {number}")
    return f"{prefix}-{number:0{digits}d}"


def make_sa_id_number(rng: random.Random, date_of_birth: date, *, is_female: bool) -> str:
    """Build a 13-digit SA ID number: YYMMDD, gender sequence, citizenship, 8, check digit."""
    # 0000-4999 is female, 5000-9999 is male.
    sequence = rng.randint(0, 4999) if is_female else rng.randint(5000, 9999)
    citizenship = "0"  # SA citizen
    payload = f"{date_of_birth:%y%m%d}{sequence:04d}{citizenship}8"
    return payload + luhn_check_digit(payload)


def make_phone_number(rng: random.Random) -> str:
    """Build an SA mobile number in international format, e.g. ``+27821234567``."""
    return f"+27{rng.choice(MOBILE_PREFIXES)}{rng.randrange(10**7):07d}"


def make_account_number(rng: random.Random) -> str:
    """Build a 10-digit account number that does not start with zero."""
    return str(rng.randrange(10**9, 10**10))


def make_company_reg_number(rng: random.Random, registration_year: int) -> str:
    """Build a CIPC-style registration number, e.g. ``2015/123456/07``."""
    return f"{registration_year}/{rng.randrange(10**6):06d}/{PRIVATE_COMPANY_SUFFIX}"


def make_card_token(rng: random.Random) -> str:
    """Build a random token that stands in for a card number."""
    return f"tok_{rng.getrandbits(96):024x}"


def make_pan_last4(rng: random.Random) -> str:
    """Build the last 4 digits shown to analysts."""
    return f"{rng.randrange(10**4):04d}"


def make_device_fingerprint(rng: random.Random) -> str:
    """Build a 64-character hex string that stands in for a device hash."""
    return f"{rng.getrandbits(256):064x}"
