from __future__ import annotations

import re
from decimal import Decimal

_INTEGER_PA = re.compile(r'[+-]?[0-9]+')


def parse_pressure_pa(raw: str) -> int | None:
    if raw == '':
        return None
    if not isinstance(raw, str) or _INTEGER_PA.fullmatch(raw) is None:
        raise ValueError('pressure must be an integer in Pa or an empty field')
    return int(Decimal(raw))


def pa_to_kpa(value: int | None) -> Decimal | None:
    if value is None:
        return None
    if value == 0:
        return Decimal(0)
    sign, digits, exponent = Decimal(value).as_tuple()
    exponent -= 3
    while exponent < 0 and digits[-1] == 0:
        digits = digits[:-1]
        exponent += 1
    return Decimal((sign, digits, exponent))
