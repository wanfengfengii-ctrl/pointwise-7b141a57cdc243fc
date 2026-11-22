from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from threading import RLock


class BorrowSettledError(RuntimeError):
    """Raised when a command belonging to an already settled borrow is delivered."""

    def __init__(self, borrow_id: str):
        self.code = 'BORROW_SETTLED'
        self.borrow_id = borrow_id
        super().__init__(self.code)


@dataclass
class BorrowLedger:
    """Session-scoped record of durable borrow attempts.

    The ledger lives on the in-memory :class:`~portal.render.Portal` session, so a
    reopened capture spool can consult it while a brand new portal session cannot
    claim attempts from a previous session.  Commands of settled borrows are
    refused here even when the caller re-invokes the exact closure the original
    attempt prepared.
    """

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    _lock: RLock = field(default_factory=RLock, repr=False)
    _active: dict[str, dict] = field(default_factory=dict, repr=False)
    _settled: set[str] = field(default_factory=set, repr=False)

    def begin(self, borrow_id: str, portal_id: str) -> None:
        with self._lock:
            if borrow_id in self._settled:
                raise BorrowSettledError(borrow_id)
            if borrow_id in self._active:
                raise BorrowSettledError(borrow_id)
            self._active[borrow_id] = {'portal_id': portal_id, 'collecting': True}

    def note_open(self, borrow_id: str) -> None:
        with self._lock:
            record = self._active.get(borrow_id)
            if record is not None:
                record['collecting'] = False

    def settle(self, borrow_id: str) -> None:
        with self._lock:
            self._active.pop(borrow_id, None)
            self._settled.add(borrow_id)

    def is_active(self, borrow_id: str) -> bool:
        with self._lock:
            return borrow_id in self._active

    def is_collecting(self, borrow_id: str) -> bool:
        with self._lock:
            record = self._active.get(borrow_id)
            return record is not None and record['collecting']

    def has_active(self, portal_id: str) -> bool:
        with self._lock:
            return any(item['portal_id'] == portal_id for item in self._active.values())

    def has_session(self, borrow_id: str) -> bool:
        with self._lock:
            return borrow_id in self._active or borrow_id in self._settled

    def is_settled(self, borrow_id: str) -> bool:
        with self._lock:
            return borrow_id in self._settled

    def guard(self, borrow_id: str, execute):
        """Deliver ``execute`` only while the borrow still owns the view."""
        with self._lock:
            terminated = borrow_id in self._settled or borrow_id not in self._active
        if terminated:
            raise BorrowSettledError(borrow_id)
        return execute()
