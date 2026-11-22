from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from portal.view import validate_scope

_SOURCE_COLUMNS = ('region', 'quarter', 'inspection_id', 'pressure_pa')
_IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,95}')


def require_identifier(value: str, field: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f'invalid {field}: {value!r}')


@dataclass(frozen=True)
class InspectionRecord:
    region: str
    quarter: int
    inspection_id: str
    pressure_pa: str

    def __post_init__(self) -> None:
        validate_scope(self.region, self.quarter)
        if not isinstance(self.inspection_id, str) or not isinstance(self.pressure_pa, str):
            raise ValueError('source fields must be text')


def load_records(path: str | Path) -> tuple[InspectionRecord, ...]:
    records = []
    with Path(path).open(encoding='utf-8', newline='') as source:
        reader = csv.reader(source, strict=True)
        if tuple(next(reader, ())) != _SOURCE_COLUMNS:
            raise ValueError('invalid source CSV header')
        for ordinal, columns in enumerate(reader, 1):
            if len(columns) != 4 or columns[1] not in ('1', '2', '3', '4'):
                raise ValueError(f'invalid source record {ordinal}')
            region, quarter, inspection_id, pressure = columns
            try:
                records.append(InspectionRecord(region, int(quarter), inspection_id, pressure))
            except ValueError as exc:
                raise ValueError(f'invalid source record {ordinal}: {exc}') from exc
    return tuple(records)


def select_records(
    records: Iterable[InspectionRecord], region: str, quarter: int
) -> tuple[InspectionRecord, ...]:
    validate_scope(region, quarter)
    return tuple(record for record in records if (record.region, record.quarter) == (region, quarter))
