from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from capture.orders import CaptureOrder
from portal.download import DownloadArtifact
from portal.events import FilterWrite, PortalEventLog, RenderEvent
from portal.records import require_identifier
from portal.view import validate_scope


@dataclass(frozen=True)
class RenderEvidence:
    order_id: str
    portal_id: str
    export_id: str
    render_sequence: int
    region: str
    quarter: int
    file_digest: str

    def __post_init__(self) -> None:
        require_identifier(self.order_id, 'order_id')
        require_identifier(self.portal_id, 'portal_id')
        require_identifier(self.export_id, 'export_id')
        validate_scope(self.region, self.quarter)
        if type(self.render_sequence) is not int or self.render_sequence < 1:
            raise ValueError('invalid render sequence')
        if not isinstance(self.file_digest, str) or re.fullmatch(r'[0-9a-f]{64}', self.file_digest) is None:
            raise ValueError('invalid SHA-256 digest')


def build_render_evidence(
    order: CaptureOrder, artifact: DownloadArtifact, events: PortalEventLog
) -> RenderEvidence:
    if order.portal_id != artifact.portal_id or events.portal_id != artifact.portal_id:
        raise ValueError('download does not belong to the order portal')
    source = events.events_through(artifact.render_sequence)
    marker = source[-1] if source else None
    digest = hashlib.sha256(artifact.content).hexdigest()
    if not isinstance(marker, RenderEvent) or (marker.export_id, marker.file_digest) != (artifact.export_id, digest):
        raise ValueError('download does not match its render marker')
    values = dict(events.initial_view)
    for position, event in enumerate(source, 1):
        if event.sequence != position:
            raise ValueError('incomplete portal recording')
        if isinstance(event, FilterWrite):
            values[event.field] = event.value
    return RenderEvidence(
        order.order_id, artifact.portal_id, artifact.export_id, marker.sequence,
        values['region'], values['quarter'], digest,
    )
