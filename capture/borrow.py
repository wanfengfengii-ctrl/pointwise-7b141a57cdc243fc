from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from capture.orders import CaptureOrder
from capture.spool import (
    load_borrow_items,
    mark_borrow_settled,
    record_borrow,
)
from portal.view import (
    FILTER_FIELDS,
    FilterPosition,
    PortalView,
    VersionConflict,
    edit_filter,
    read_filter,
)


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


# ---------------------------------------------------------------------------
# Durable, resumable borrows tracked by borrow id across spool reopen cycles.
# ---------------------------------------------------------------------------


def borrow_actor(borrow_id: str) -> str:
    return f'capture/borrow/{borrow_id}'


def default_send_edit(phase, field_name, execute):
    return execute()


def plan_durable_borrow(
    spool,
    portal,
    order: CaptureOrder,
    borrow_id: str,
    send_edit: Callable,
) -> str:
    """Persist every intended write, then deliver the borrow filter commands.

    The previous position and write intent are recorded before any command is
    sent.  A command whose reply is lost has already been durably planned, and a
    command that never executes simply leaves no matching write behind.
    """
    actor = borrow_actor(borrow_id)
    ledger = portal.ledger
    planned: list[dict] = []
    for field_name in FILTER_FIELDS:
        previous = read_filter(portal.view, field_name)
        desired = getattr(order, field_name)
        if previous.value == desired:
            continue
        planned.append({
            'field': field_name,
            'target_value': desired,
            'previous_value': previous.value,
            'previous_version': previous.version,
        })
    # Persistence happens first: after this point recovery can reconcile every
    # planned item even if the collector process disappears immediately.
    record_borrow(spool, borrow_id, portal.portal_id, ledger.session_id, order, planned)
    ledger.begin(borrow_id, portal.portal_id)

    try:
        for item in planned:
            field_name = item['field']
            target = item['target_value']
            expected = item['previous_version']

            def execute(field_name=field_name, target=target, expected=expected):
                return ledger.guard(
                    borrow_id,
                    lambda: edit_filter(
                        portal.view, field_name, target, actor, expected_version=expected
                    ),
                )

            send_edit('borrow', field_name, execute)
    finally:
        ledger.note_open(borrow_id)
    return actor


def _restore_item(portal, borrow_id, actor, item, send_edit):
    field_name = item['field']
    previous_value = item['previous_value']
    current = read_filter(portal.view, field_name)
    # Only a position still last written by this borrow is ours to return.
    # A human re-selection (even to the same value), a never-executed write,
    # and an already-completed restore are all left untouched.
    if current.actor != actor or current.value == previous_value:
        return False
    expected = current.version

    def execute():
        return portal.ledger.guard(
            borrow_id,
            lambda: edit_filter(
                portal.view, field_name, previous_value, actor, expected_version=expected
            ),
        )

    send_edit('restore', field_name, execute)
    return True


def settle_borrow(spool, portal, borrow_id: str, send_edit: Callable) -> str:
    """Restore only the items this borrow last wrote and close the attempt.

    Returns ``settled`` when every owned item has been returned (or needed no
    return), and ``busy`` when a concurrent filter write keeps the ownership
    check unconfirmable so the caller may retry after reopening.
    """
    actor = borrow_actor(borrow_id)
    items = load_borrow_items(spool, borrow_id)
    attempts = 0
    while True:
        blocked = False
        for item in items:
            field_name = item['field']
            current = read_filter(portal.view, field_name)
            if current.actor != actor or current.value == item['previous_value']:
                continue
            try:
                _restore_item(portal, borrow_id, actor, item, send_edit)
            except VersionConflict:
                blocked = True
        if not blocked:
            break
        attempts += 1
        if attempts >= 2:
            return 'busy'
    mark_borrow_settled(spool, borrow_id)
    portal.ledger.settle(borrow_id)
    return 'settled'


__all__ = [
    'BorrowedFilter',
    'BorrowState',
    'begin_borrow',
    'borrow_actor',
    'default_send_edit',
    'plan_durable_borrow',
    'release_borrow',
    'settle_borrow',
]
