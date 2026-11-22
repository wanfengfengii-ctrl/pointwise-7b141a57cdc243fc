from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from portal.records import require_identifier
from portal.view import validate_scope


def validate_shift(shift: str) -> None:
    if not isinstance(shift, str):
        raise ValueError('shift must have the form YYYY-MM-DD/day or YYYY-MM-DD/night')
    stamp, separator, period = shift.partition('/')
    if not separator or period not in ('day', 'night'):
        raise ValueError(f'invalid shift: {shift!r}')
    try:
        normalized = date.fromisoformat(stamp).isoformat()
    except ValueError:
        raise ValueError(f'invalid shift: {shift!r}') from None
    if stamp != normalized:
        raise ValueError(f'invalid shift: {shift!r}')


@dataclass(frozen=True)
class CaptureOrder:
    order_id: str
    portal_id: str
    region: str
    quarter: int
    shift: str
    position: int

    def __post_init__(self) -> None:
        require_identifier(self.order_id, 'order_id')
        require_identifier(self.portal_id, 'portal_id')
        validate_scope(self.region, self.quarter)
        validate_shift(self.shift)
        if type(self.position) is not int or not 1 <= self.position <= 2**63 - 1:
            raise ValueError('position must be a positive SQLite integer')


def make_order(
    order_id: str, portal_id: str, region: str, quarter: int, shift: str, position: int
) -> CaptureOrder:
    return CaptureOrder(order_id, portal_id, region, quarter, shift, position)
