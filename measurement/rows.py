from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Iterator


class RowFormatError(ValueError):
    pass


@dataclass(frozen=True)
class RawMeasurementRow:
    portal_id: str
    export_id: str
    record_number: int
    inspection_id: str
    raw_pressure: str


def read_rows(content: bytes, portal_id: str, export_id: str) -> Iterator[RawMeasurementRow]:
    if not isinstance(content, bytes):
        raise RowFormatError('original CSV must be bytes')
    try:
        text = content.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise RowFormatError(f'invalid UTF-8 at byte {exc.start}') from exc
    reader = csv.reader(io.StringIO(text, newline=''), strict=True)
    try:
        if next(reader, None) != ['inspection_id', 'pressure_pa']:
            raise RowFormatError('invalid measurement CSV header')
        for number, columns in enumerate(reader, 1):
            if len(columns) != 2:
                raise RowFormatError(f'data record {number} must have two fields')
            yield RawMeasurementRow(portal_id, export_id, number, columns[0], columns[1])
    except csv.Error as exc:
        raise RowFormatError(f'invalid CSV at physical line {reader.line_num}: {exc}') from exc
