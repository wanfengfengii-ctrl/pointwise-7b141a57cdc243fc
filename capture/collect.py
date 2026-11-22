from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable

from capture.borrow import BorrowState, begin_borrow, release_borrow
from capture.evidence import build_render_evidence
from capture.orders import CaptureOrder
from capture.spool import Delivery, load_delivery, stage_delivery
from portal.download import render_download
from portal.events import PortalError
from portal.records import require_identifier
from portal.render import Portal, render_export, request_export


@dataclass(frozen=True)
class CollectResult:
    status: str
    delivery: Delivery | None = None
    code: str | None = None


def collect_order(
    spool: sqlite3.Connection,
    portal: Portal,
    order: CaptureOrder,
    export_id: str,
    drive: Callable[[Portal, str], None] | None = None,
    *,
    before_restore: Callable[[str], None] | None = None,
) -> CollectResult:
    require_identifier(export_id, 'export_id')
    if order.portal_id != portal.portal_id:
        return CollectResult('failed', code='ORDER_PORTAL_MISMATCH')
    borrowed = BorrowState(f'capture/{order.order_id}/{export_id}')
    try:
        saved = load_delivery(spool, order.portal_id, export_id)
        if saved is not None:
            if saved.order != order:
                return CollectResult('failed', code='CAPTURE_BINDING_CONFLICT')
            return CollectResult('ready', saved)
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
        finally:
            release_borrow(portal.view, borrowed, before_restore)
        delivery = stage_delivery(spool, order, artifact, evidence)
    except PortalError as exc:
        return CollectResult('failed', code=exc.code)
    except sqlite3.Error:
        return CollectResult('failed', code='DELIVERY_STORAGE_FAILED')
    except ValueError:
        return CollectResult('failed', code='INVALID_CAPTURE_EVIDENCE')
    return CollectResult('ready', delivery)
