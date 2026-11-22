from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass, replace
from typing import Iterable

from portal.events import PortalError, PortalEventLog
from portal.records import InspectionRecord, require_identifier, select_records
from portal.view import PortalView, read_filter, validate_scope


@dataclass(frozen=True)
class ExportInstance:
    export_id: str
    request_sequence: int
    render_sequence: int | None = None
    content: bytes | None = None
    downloadable: bool = True


class Portal:
    def __init__(self, portal_id: str, records: Iterable[InspectionRecord], region: str, quarter: int):
        require_identifier(portal_id, 'portal_id')
        validate_scope(region, quarter)
        self.portal_id = portal_id
        self.records = tuple(records)
        self.events = PortalEventLog(portal_id, region, quarter)
        self.view = PortalView(self.events)
        self._exports: dict[str, ExportInstance] = {}

    def _export(self, export_id: str) -> ExportInstance:
        self.events.require_open()
        try:
            return self._exports[export_id]
        except KeyError:
            raise PortalError('UNKNOWN_EXPORT') from None

    def make_unavailable(self, export_id: str) -> None:
        with self.events.lock:
            current = self._export(export_id)
            self._exports[export_id] = replace(current, downloadable=False)

    def close(self) -> None:
        self.events.close()


def request_export(portal: Portal, export_id: str) -> ExportInstance:
    require_identifier(export_id, 'export_id')
    with portal.events.lock:
        portal.events.require_open()
        if export_id in portal._exports:
            raise PortalError('DUPLICATE_EXPORT')
        request = portal.events.record_request(export_id)
        export = ExportInstance(export_id, request.sequence)
        portal._exports[export_id] = export
        return export


def render_export(portal: Portal, export_id: str) -> ExportInstance:
    with portal.events.lock:
        request = portal._export(export_id)
        if request.render_sequence is not None:
            raise PortalError('EXPORT_ALREADY_RENDERED')
        region = read_filter(portal.view, 'region').value
        quarter = read_filter(portal.view, 'quarter').value
        rows = select_records(portal.records, region, quarter)
        stream = io.StringIO(newline='')
        writer = csv.writer(stream, lineterminator='\r\n')
        writer.writerow(('inspection_id', 'pressure_pa'))
        writer.writerows((row.inspection_id, row.pressure_pa) for row in rows)
        content = stream.getvalue().encode('utf-8')
        marker = portal.events.record_render(export_id, hashlib.sha256(content).hexdigest())
        export = replace(request, render_sequence=marker.sequence, content=content)
        portal._exports[export_id] = export
        return export
