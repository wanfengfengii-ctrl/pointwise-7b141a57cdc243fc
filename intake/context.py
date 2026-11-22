from __future__ import annotations

from collections.abc import Mapping

from capture.evidence import RenderEvidence
from intake.files import ReceivedFile

ReceivedContext = RenderEvidence


def read_received_context(value: ReceivedContext | Mapping[str, object]) -> ReceivedContext:
    if isinstance(value, ReceivedContext):
        return value
    if not isinstance(value, Mapping):
        raise ValueError('expected render evidence')
    try:
        return ReceivedContext(**dict(value))
    except TypeError as exc:
        raise ValueError('invalid render evidence fields') from exc


def match_context(file: ReceivedFile, context: ReceivedContext) -> str | None:
    if file.identity != (context.order_id, context.portal_id, context.export_id):
        return 'MATERIAL_IDENTITY_MISMATCH'
    if file.file_digest != context.file_digest:
        return 'FILE_DIGEST_MISMATCH'
    return None
