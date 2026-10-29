"""Money types and helpers.

Two distinct Pydantic-annotated Decimal types:
  - Money      : for request amounts, item amounts, payouts.  Must be > 0, ≤ 99,999,999.99, 2 d.p.
  - Allocation : for project budget buckets.  Must be ≥ 0, up to 14 digits total, 2 d.p.

q2()   : round any numeric value half-up to 2 decimal places.
ZERO   : Decimal("0.00") sentinel.
MAX_AMOUNT : ceiling for Money values.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from pydantic import Field

ZERO = Decimal("0.00")
MAX_AMOUNT = Decimal("99999999.99")

# Used for transaction amounts, item amounts, payouts — must be strictly positive.
Money = Annotated[
    Decimal,
    Field(gt=Decimal("0"), le=MAX_AMOUNT, decimal_places=2),
]

# Used for project budget buckets — may be zero (e.g. on project creation).
Allocation = Annotated[
    Decimal,
    Field(ge=Decimal("0"), max_digits=14, decimal_places=2),
]


def q2(value: Decimal | int | str) -> Decimal:
    """Round *value* half-up to 2 decimal places."""
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
