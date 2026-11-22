from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from capture.orders import CaptureOrder
from portal.view import FILTER_FIELDS, FilterPosition, PortalView, VersionConflict, edit_filter, read_filter


@dataclass(frozen=True)
class BorrowedFilter:
    previous: FilterPosition
    written: FilterPosition


@dataclass
class BorrowState:
    actor: str
    items: list[BorrowedFilter] = field(default_factory=list)


def begin_borrow(view: PortalView, order: CaptureOrder, state: BorrowState) -> None:
    for field_name in FILTER_FIELDS:
        previous = read_filter(view, field_name)
        desired = getattr(order, field_name)
        if previous.value == desired:
            continue
        written = edit_filter(view, field_name, desired, state.actor, expected_version=previous.version)
        state.items.append(BorrowedFilter(previous, written))


def release_borrow(
    view: PortalView, state: BorrowState, before_restore: Callable[[str], None] | None = None
) -> None:
    for item in state.items:
        name = item.written.field
        current = read_filter(view, name)
        if current != item.written:
            continue
        if before_restore is not None:
            before_restore(name)
        try:
            edit_filter(view, name, item.previous.value, state.actor, expected_version=current.version)
        except VersionConflict:
            continue
