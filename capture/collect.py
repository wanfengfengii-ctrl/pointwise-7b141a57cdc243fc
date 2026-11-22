from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable

from capture.borrow import (
    BorrowState,
    begin_borrow,
    default_send_edit,
    plan_durable_borrow,
    release_borrow,
    settle_borrow,
)
from capture.evidence import build_render_evidence
from capture.orders import CaptureOrder
from capture.spool import (
    Delivery,
    ensure_delivery_schema,
    has_open_borrow,
    load_borrow,
    load_delivery,
    stage_delivery,
)
from portal.download import render_download
from portal.events import PortalError
from portal.ledger import BorrowSettledError
from portal.records import require_identifier
from portal.render import Portal, render_export, request_export
from portal.wire import CaptureLinkLost


@dataclass(frozen=True)
class CollectResult:
    status: str
    delivery: Delivery | None = None
    code: str | None = None


def _preflight(spool: sqlite3.Connection, portal: Portal, order: CaptureOrder, export_id: str,
               borrow_id: str | None) -> CollectResult | None:
    if order.portal_id != portal.portal_id:
        return CollectResult('failed', code='ORDER_PORTAL_MISMATCH')
    if borrow_id is not None:
        record = load_borrow(spool, borrow_id)
        if record is not None:
            if record['status'] == 'settled':
                # A settled id can never fund a new borrow; reclamation of its
                # saved delivery is handled earlier by the caller's export id.
                return CollectResult('failed', code='BORROW_SETTLED')
            if record['session_id'] != portal.ledger.session_id or record['portal_id'] != portal.portal_id:
                return CollectResult('failed', code='BORROW_SESSION_CONFLICT')
            return CollectResult('failed', code='BORROW_ALREADY_OPEN')
    if has_open_borrow(spool, portal.portal_id) or portal.ledger.has_active(portal.portal_id):
        return CollectResult('busy')
    return None


def collect_order(
    spool: sqlite3.Connection,
    portal: Portal,
    order: CaptureOrder,
    export_id: str,
    drive: Callable[[Portal, str], None] | None = None,
    *,
    before_restore: Callable[[str], None] | None = None,
    borrow_id: str | None = None,
    send_edit: Callable | None = None,
) -> CollectResult:
    require_identifier(export_id, 'export_id')
    if borrow_id is not None:
        require_identifier(borrow_id, 'borrow_id')
    ensure_delivery_schema(spool)
    sender = default_send_edit if send_edit is None else send_edit

    saved = load_delivery(spool, order.portal_id, export_id)
    if saved is not None:
        if saved.order != order:
            return CollectResult('failed', code='CAPTURE_BINDING_CONFLICT')
        return CollectResult('ready', saved)

    blocked = _preflight(spool, portal, order, export_id, borrow_id)
    if blocked is not None:
        return blocked

    if borrow_id is None:
        return _collect_legacy(spool, portal, order, export_id, drive, before_restore)
    return _collect_durable(spool, portal, order, export_id, drive, borrow_id, sender)


def _collect_legacy(spool, portal, order, export_id, drive, before_restore) -> CollectResult:
    borrowed = BorrowState(f'capture/{order.order_id}/{export_id}')
    try:
        begin_borrow(portal.view, order, borrowed)
        request_export(portal, export_id)
        if drive is None:
            render_export(portal, export_id)
        else:
            drive(portal, export_id)
        artifact = render_download(portal, export_id)
        if artifact is None:
            return CollectResult('failed', code='EXPORT_PENDING')
        evidence = build_render_evidence(order, artifact, portal.events)
    except PortalError as exc:
        return CollectResult('failed', code=exc.code)
    finally:
        release_borrow(portal.view, borrowed, before_restore)
    try:
        delivery = stage_delivery(spool, order, artifact, evidence)
    except sqlite3.Error:
        return CollectResult('failed', code='DELIVERY_STORAGE_FAILED')
    except ValueError:
        return CollectResult('failed', code='INVALID_CAPTURE_EVIDENCE')
    return CollectResult('ready', delivery)


def _collect_durable(spool, portal, order, export_id, drive, borrow_id, sender) -> CollectResult:
    try:
        plan_durable_borrow(spool, portal, order, borrow_id, sender)
        request_export(portal, export_id)
        if drive is None:
            render_export(portal, export_id)
        else:
            drive(portal, export_id)
        artifact = render_download(portal, export_id)
        if artifact is None:
            settle_borrow(spool, portal, borrow_id, sender)
            return CollectResult('failed', code='EXPORT_PENDING')
        evidence = build_render_evidence(order, artifact, portal.events)
        delivery = stage_delivery(spool, order, artifact, evidence)
    except CaptureLinkLost:
        # The collector call is over; the durable borrow stays open for recovery.
        raise
    except PortalError as exc:
        if portal.ledger.is_active(borrow_id) and not portal.ledger.is_collecting(borrow_id):
            try:
                settle_borrow(spool, portal, borrow_id, sender)
            except (PortalError, BorrowSettledError):
                pass
        return CollectResult('failed', code=exc.code)
    except sqlite3.Error:
        return CollectResult('failed', code='DELIVERY_STORAGE_FAILED')
    except ValueError:
        return CollectResult('failed', code='INVALID_CAPTURE_EVIDENCE')
    outcome = settle_borrow(spool, portal, borrow_id, sender)
    if outcome != 'settled':
        return CollectResult('busy', delivery)
    return CollectResult('ready', delivery)


def recover_borrow(
    spool: sqlite3.Connection,
    portal: Portal,
    borrow_id: str,
    *,
    send_edit: Callable | None = None,
) -> str:
    """Resume settlement of a borrow whose collector lost the link.

    Returns ``settled`` once the restore obligation is closed, ``unknown`` when
    this portal session never saw the attempt, and ``busy`` when settlement is
    still unconfirmable and the caller may reopen and retry.
    """
    require_identifier(borrow_id, 'borrow_id')
    ensure_delivery_schema(spool)
    sender = default_send_edit if send_edit is None else send_edit
    record = load_borrow(spool, borrow_id)
    if record is None:
        return 'unknown'
    if record['status'] == 'settled':
        if portal.ledger.has_session(borrow_id):
            return 'settled'
        return 'unknown'
    if record['session_id'] != portal.ledger.session_id or record['portal_id'] != portal.portal_id:
        return 'unknown'
    if portal.ledger.is_collecting(borrow_id):
        return 'busy'
    return settle_borrow(spool, portal, borrow_id, sender)
