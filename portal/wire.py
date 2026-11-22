from __future__ import annotations

from typing import Callable


class CaptureLinkLost(BaseException):
    """The scripted collector is gone while the portal stays up."""


class ScriptedLink:
    """Retain portal deliveries independently of any collector call."""

    def __init__(self):
        self._armed = None
        self._held = []

    def arm(self, phase: str, field: str, mode: str) -> None:
        if self._armed is not None:
            raise ValueError("a cut is already armed")
        if phase not in ("borrow", "restore") or field not in ("region", "quarter"):
            raise ValueError("unknown edit slot")
        if mode not in ("hold", "drop_reply"):
            raise ValueError("unknown link mode")
        self._armed = (phase, field, mode)

    def send_edit(self, phase: str, field: str, execute: Callable[[], object]) -> object:
        if self._armed is None or self._armed[:2] != (phase, field):
            return execute()
        _, _, mode = self._armed
        self._armed = None
        self._held.append(execute)
        if mode == "drop_reply":
            execute()
        raise CaptureLinkLost(f"{phase}:{field}:{mode}")

    def deliver(self, index: int = -1) -> object:
        return self._held[index]()
