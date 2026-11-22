from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from decimal import Decimal

from archive.receipts import Receipt, make_first_receipt, read_receipt
from archive.store import publication_transaction
from capture.evidence import RenderEvidence
from capture.orders import CaptureOrder
from measurement.summary import MeasurementBatch


@dataclass(frozen=True)
class PublicationResult:
    status: str
    code: str | None = None
    result_id: str | None = None
    receipt: Receipt | None = None


def existing_publication(
    connection: sqlite3.Connection, order: CaptureOrder, export_id: str, evidence: RenderEvidence | None = None
) -> PublicationResult | None:
    saved = connection.execute('''
        SELECT a.*, r.receipt_id FROM archive_results AS a
        JOIN receipts AS r ON r.result_id = a.result_id WHERE a.order_id = ?
    ''', (order.order_id,)).fetchone()
    if saved is None:
        return None
    if (saved['portal_id'], saved['region'], saved['quarter'], saved['shift'], saved['position']) != (
        order.portal_id, order.region, order.quarter, order.shift, order.position,
    ):
        return PublicationResult('rejected', 'ORDER_FACTS_CONFLICT')
    if saved['export_id'] != export_id:
        return PublicationResult('rejected', 'ORDER_ALREADY_ARCHIVED')
    if evidence is not None and json.loads(saved['evidence_json']) != asdict(evidence):
        return PublicationResult('rejected', 'ARCHIVE_EVIDENCE_CONFLICT')
    return PublicationResult('accepted', result_id=saved['result_id'], receipt=read_receipt(saved))


def publish_archive(
    connection: sqlite3.Connection, order: CaptureOrder, evidence: RenderEvidence, batch: MeasurementBatch
) -> PublicationResult:
    if (evidence.order_id, evidence.portal_id) != (order.order_id, order.portal_id):
        return PublicationResult('rejected', 'ARCHIVE_IDENTITY_MISMATCH')
    try:
        with publication_transaction(connection):
            existing = existing_publication(connection, order, evidence.export_id, evidence)
            if existing is not None:
                return existing
            occupied = connection.execute('''
                SELECT 1 FROM archive_results
                WHERE (portal_id = ? AND export_id = ?) OR (shift = ? AND position = ?)
            ''', (order.portal_id, evidence.export_id, order.shift, order.position)).fetchone()
            if occupied is not None:
                return PublicationResult('rejected', 'ARCHIVE_BINDING_CONFLICT')
            result_id = f'A-{order.order_id}'
            receipt = make_first_receipt(result_id, evidence)
            connection.execute('''
                INSERT INTO archive_results (
                    result_id, order_id, portal_id, export_id, region, quarter, shift, position,
                    evidence_json, file_digest, missing_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (result_id, order.order_id, order.portal_id, evidence.export_id, order.region,
                  order.quarter, order.shift, order.position, json.dumps(asdict(evidence), sort_keys=True),
                  evidence.file_digest, batch.missing_count))
            details = []
            for item in batch.details:
                row = item.row
                if (row.portal_id, row.export_id) != (evidence.portal_id, evidence.export_id):
                    raise ValueError('measurement row belongs to another export')
                pa = None if item.pressure_pa is None else format(Decimal(item.pressure_pa), 'f')
                kpa = None if item.pressure_kpa is None else format(item.pressure_kpa, 'f')
                details.append((result_id, row.record_number, row.inspection_id, row.raw_pressure, pa, kpa))
            connection.executemany('''
                INSERT INTO result_rows (
                    result_id, record_number, inspection_id, raw_pressure, pressure_pa, pressure_kpa
                ) VALUES (?, ?, ?, ?, ?, ?)
            ''', details)
            connection.execute('INSERT INTO receipts (receipt_id, result_id) VALUES (?, ?)', (
                receipt.receipt_id, result_id,
            ))
    except sqlite3.Error:
        return PublicationResult('failed', 'PUBLICATION_FAILED')
    except ValueError:
        return PublicationResult('rejected', 'ARCHIVE_ROW_IDENTITY_MISMATCH')
    return PublicationResult('accepted', result_id=result_id, receipt=receipt)
