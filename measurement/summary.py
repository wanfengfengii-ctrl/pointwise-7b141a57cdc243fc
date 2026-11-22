from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from measurement.rows import RawMeasurementRow


@dataclass(frozen=True)
class MeasurementDetail:
    row: RawMeasurementRow
    pressure_pa: int | None
    pressure_kpa: Decimal | None


@dataclass(frozen=True)
class MeasurementBatch:
    details: tuple[MeasurementDetail, ...]
    missing_count: int


def summarize_batch(details: Iterable[MeasurementDetail]) -> MeasurementBatch:
    ordered = tuple(details)
    return MeasurementBatch(ordered, sum(detail.pressure_pa is None for detail in ordered))
