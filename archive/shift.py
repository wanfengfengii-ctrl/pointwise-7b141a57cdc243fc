from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from capture.orders import validate_shift


@dataclass(frozen=True)
class ShiftEntry:
    shift: str
    position: int
    order_id: str
    result_id: str
    receipt_id: str
    missing_count: int


def shift_manifest(connection: sqlite3.Connection, shift: str) -> tuple[ShiftEntry, ...]:
    validate_shift(shift)
    entries = connection.execute('''
        SELECT a.shift, a.position, a.order_id, a.result_id, r.receipt_id, a.missing_count
        FROM archive_results AS a JOIN receipts AS r ON r.result_id = a.result_id
        WHERE a.shift = ? ORDER BY a.position
    ''', (shift,))
    return tuple(ShiftEntry(*entry) for entry in entries)
