from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass

from archive.publish import PublicationResult, existing_publication, publish_archive
from archive.receipts import Receipt
from capture.orders import CaptureOrder
from intake.context import ReceivedContext, read_received_context
from intake.eligibility import assess_eligibility
from intake.files import ReceivedFile
from intake.store import load_candidate, read_registered_order, record_rejection, store_material
from measurement.validation import MeasurementError, parse_measurements
from portal.records import require_identifier


@dataclass(frozen=True)
class IntakeResult:
    status: str
    code: str | None = None
    result_id: str | None = None
    receipt: Receipt | None = None
    detail: str | None = None


def _from_publication(result: PublicationResult) -> IntakeResult:
    return IntakeResult(result.status, result.code, result.result_id, result.receipt)


def receive_material(
    connection: sqlite3.Connection,
    order: CaptureOrder,
    export_id: str,
    material: ReceivedFile | ReceivedContext | Mapping[str, object] | None = None,
    *,
    process: bool = True,
) -> IntakeResult:
    require_identifier(export_id, 'export_id')
    if material is not None and not isinstance(material, ReceivedFile):
        try:
            material = read_received_context(material)
        except ValueError as exc:
            return IntakeResult('rejected', 'INVALID_CONTEXT', detail=str(exc))
    if material is not None and (material.order_id, material.portal_id, material.export_id) != (
        order.order_id, order.portal_id, export_id,
    ):
        return IntakeResult('rejected', 'MATERIAL_IDENTITY_MISMATCH')

    try:
        prior = existing_publication(connection, order, export_id)
        if prior is not None and prior.status == 'rejected':
            return _from_publication(prior)
        if material is not None:
            conflict = store_material(connection, order, export_id, material)
            if conflict:
                return IntakeResult('rejected', conflict)
        fixed = read_registered_order(connection, order.order_id)
        if fixed is not None and fixed != order:
            return IntakeResult('rejected', 'ORDER_FACTS_CONFLICT')
        candidate = load_candidate(connection, order, export_id)
        if candidate.rejection is not None:
            return IntakeResult('rejected', candidate.rejection)
        if not process:
            return IntakeResult('pending', 'DEFERRED')
        file, context = candidate.file, candidate.context
        decision = assess_eligibility(order, file, context)
        if decision.status == 'pending':
            return IntakeResult(decision.status, decision.code)
        if decision.status == 'rejected':
            if decision.code == 'EXPORT_CONTEXT_MISMATCH':
                record_rejection(connection, order, export_id, decision.code)
            return IntakeResult(decision.status, decision.code)
        if prior is not None:
            replay = existing_publication(connection, order, export_id, context)
            return _from_publication(replay)
        try:
            batch = parse_measurements(file.content, file.portal_id, file.export_id)
        except MeasurementError as exc:
            record_rejection(connection, order, export_id, 'INVALID_MEASUREMENT_CSV')
            return IntakeResult('rejected', 'INVALID_MEASUREMENT_CSV', detail=str(exc))
    except sqlite3.Error:
        return IntakeResult('failed', 'INTAKE_STORAGE_FAILED')
    return _from_publication(publish_archive(connection, order, context, batch))
