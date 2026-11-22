from __future__ import annotations

from dataclasses import dataclass

from portal.events import PortalError, PortalEventLog

FILTER_FIELDS = ('region', 'quarter')
REGIONS = ('east', 'west', 'north', 'south')


class VersionConflict(PortalError):
    def __init__(self, field: str, expected: int, actual: int):
        self.field = field
        self.expected = expected
        self.actual = actual
        super().__init__('FILTER_VERSION_CONFLICT')


@dataclass(frozen=True)
class FilterPosition:
    field: str
    value: str | int
    actor: str
    version: int
    sequence: int


def validate_filter(field: str, value: str | int) -> None:
    if field == 'region' and isinstance(value, str) and value in REGIONS:
        return
    if field == 'quarter' and type(value) is int and 1 <= value <= 4:
        return
    raise ValueError(f'invalid filter {field!r}: {value!r}')


def validate_scope(region: str, quarter: int) -> None:
    validate_filter('region', region)
    validate_filter('quarter', quarter)


class PortalView:
    def __init__(self, events: PortalEventLog):
        self.events = events
        validate_scope(events.initial_view['region'], events.initial_view['quarter'])
        self._positions = {
            field: FilterPosition(field, events.initial_view[field], 'initial', 0, 0)
            for field in FILTER_FIELDS
        }

    def _read(self, field: str) -> FilterPosition:
        if field not in FILTER_FIELDS:
            raise ValueError(f'unknown filter: {field!r}')
        return self._positions[field]


def read_filter(view: PortalView, field: str) -> FilterPosition:
    with view.events.lock:
        view.events.require_open()
        return view._read(field)


def edit_filter(
    view: PortalView,
    field: str,
    value: str | int,
    actor: str,
    expected_version: int | None = None,
) -> FilterPosition:
    validate_filter(field, value)
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError('actor is required')
    if expected_version is not None and (type(expected_version) is not int or expected_version < 0):
        raise ValueError('expected_version must be a nonnegative integer')
    with view.events.lock:
        view.events.require_open()
        current = view._read(field)
        if expected_version is not None and expected_version != current.version:
            raise VersionConflict(field, expected_version, current.version)
        event = view.events.record_write(field, value, actor, current.version + 1)
        position = FilterPosition(field, value, actor, event.version, event.sequence)
        view._positions[field] = position
        return position
