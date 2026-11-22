from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from capture.evidence import RenderEvidence


@dataclass(frozen=True)
class Receipt:
    receipt_id: str
    result_id: str
    order_id: str
    portal_id: str
    export_id: str
    file_digest: str


def make_first_receipt(result_id: str, evidence: RenderEvidence) -> Receipt:
    return Receipt(
        f'R-{evidence.order_id}', result_id, evidence.order_id, evidence.portal_id,
        evidence.export_id, evidence.file_digest,
    )


def read_receipt(row: sqlite3.Row) -> Receipt:
    return Receipt(
        row['receipt_id'], row['result_id'], row['order_id'], row['portal_id'],
        row['export_id'], row['file_digest'],
    )
