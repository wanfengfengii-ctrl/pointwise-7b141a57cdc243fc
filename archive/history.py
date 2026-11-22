from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal

from archive.receipts import Receipt, read_receipt
from capture.evidence import RenderEvidence
from capture.orders import CaptureOrder
from measurement.rows import RawMeasurementRow
from measurement.summary import MeasurementDetail


@dataclass(frozen=True)
class ArchivedResult:
    result_id: str
    order: CaptureOrder
    evidence: RenderEvidence
    rows: tuple[MeasurementDetail, ...]
    missing_count: int
    receipt: Receipt


def get_archive(connection: sqlite3.Connection, result_id: str) -> ArchivedResult | None:
    header = connection.execute('''
        SELECT a.*, r.receipt_id FROM archive_results AS a
        JOIN receipts AS r ON r.result_id = a.result_id WHERE a.result_id = ?
    ''', (result_id,)).fetchone()
    if header is None:
        return None
    order = CaptureOrder(
        header['order_id'], header['portal_id'], header['region'], header['quarter'], header['shift'], header['position'],
    )
    evidence = RenderEvidence(**json.loads(header['evidence_json']))
    details = []
    for ordinal, inspection_id, raw, pa, kpa in connection.execute('''
        SELECT record_number, inspection_id, raw_pressure, pressure_pa, pressure_kpa
        FROM result_rows WHERE result_id = ? ORDER BY record_number
    ''', (result_id,)):
        source = RawMeasurementRow(evidence.portal_id, evidence.export_id, ordinal, inspection_id, raw)
        details.append(MeasurementDetail(
            source, None if pa is None else int(Decimal(pa)), None if kpa is None else Decimal(kpa),
        ))
    return ArchivedResult(result_id, order, evidence, tuple(details), header['missing_count'], read_receipt(header))
