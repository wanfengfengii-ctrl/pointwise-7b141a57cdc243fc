from __future__ import annotations

from dataclasses import dataclass

from portal.events import PortalError
from portal.render import Portal


@dataclass(frozen=True)
class DownloadArtifact:
    portal_id: str
    export_id: str
    content: bytes
    render_sequence: int


def render_download(portal: Portal, export_id: str) -> DownloadArtifact | None:
    with portal.events.lock:
        export = portal._export(export_id)
        if not export.downloadable:
            raise PortalError('EXPORT_UNAVAILABLE')
        if export.render_sequence is None:
            return None
        if export.content is None:
            raise PortalError('EXPORT_UNAVAILABLE')
        portal.events.record_download(export_id, export.render_sequence)
        return DownloadArtifact(portal.portal_id, export.export_id, export.content, export.render_sequence)
