from __future__ import annotations

import hashlib
from dataclasses import dataclass

from portal.records import require_identifier


@dataclass(frozen=True)
class ReceivedFile:
    order_id: str
    portal_id: str
    export_id: str
    content: bytes

    def __post_init__(self) -> None:
        require_identifier(self.order_id, 'order_id')
        require_identifier(self.portal_id, 'portal_id')
        require_identifier(self.export_id, 'export_id')
        if type(self.content) is not bytes:
            raise ValueError('original content must be bytes')

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.order_id, self.portal_id, self.export_id

    @property
    def file_digest(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


def read_received_file(order_id: str, portal_id: str, export_id: str, content: bytes) -> ReceivedFile:
    return ReceivedFile(order_id, portal_id, export_id, content)
