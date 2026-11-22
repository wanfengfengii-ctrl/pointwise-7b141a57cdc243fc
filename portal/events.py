from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from types import MappingProxyType


class PortalError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class FilterWrite:
    sequence: int
    field: str
    value: str | int
    actor: str
    version: int


@dataclass(frozen=True)
class ExportRequest:
    sequence: int
    export_id: str


@dataclass(frozen=True)
class RenderEvent:
    sequence: int
    export_id: str
    file_digest: str


@dataclass(frozen=True)
class DownloadEvent:
    sequence: int
    export_id: str
    render_sequence: int


SourceEvent = FilterWrite | ExportRequest | RenderEvent | DownloadEvent


class PortalEventLog:
    def __init__(self, portal_id: str, region: str, quarter: int):
        self.portal_id = portal_id
        self.initial_view = MappingProxyType({'region': region, 'quarter': quarter})
        self.lock = RLock()
        self._events: list[SourceEvent] = []
        self._closed = False

    def require_open(self) -> None:
        if self._closed:
            raise PortalError('PORTAL_CLOSED')

    def close(self) -> None:
        with self.lock:
            self._closed = True

    def record_write(self, field: str, value: str | int, actor: str, version: int) -> FilterWrite:
        with self.lock:
            self.require_open()
            event = FilterWrite(len(self._events) + 1, field, value, actor, version)
            self._events.append(event)
            return event

    def record_request(self, export_id: str) -> ExportRequest:
        with self.lock:
            self.require_open()
            event = ExportRequest(len(self._events) + 1, export_id)
            self._events.append(event)
            return event

    def record_render(self, export_id: str, file_digest: str) -> RenderEvent:
        with self.lock:
            self.require_open()
            event = RenderEvent(len(self._events) + 1, export_id, file_digest)
            self._events.append(event)
            return event

    def record_download(self, export_id: str, render_sequence: int) -> DownloadEvent:
        with self.lock:
            self.require_open()
            event = DownloadEvent(len(self._events) + 1, export_id, render_sequence)
            self._events.append(event)
            return event

    def events_through(self, sequence: int) -> tuple[SourceEvent, ...]:
        with self.lock:
            self.require_open()
            if type(sequence) is not int or not 0 <= sequence <= len(self._events):
                raise PortalError('UNKNOWN_SOURCE_POSITION')
            return tuple(self._events[:sequence])
