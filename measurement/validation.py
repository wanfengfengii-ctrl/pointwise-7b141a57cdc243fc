from __future__ import annotations

from measurement.rows import RowFormatError, read_rows
from measurement.summary import MeasurementBatch, MeasurementDetail, summarize_batch
from measurement.unitsa import pa_to_kpa, parse_pressure_pa


class MeasurementError(ValueError):
    pass


def parse_measurements(content: bytes, portal_id: str, export_id: str) -> MeasurementBatch:
    seen = set()
    details = []
    try:
        for row in read_rows(content, portal_id, export_id):
            if not row.inspection_id.strip() or row.inspection_id in seen:
                raise MeasurementError(f'data record {row.record_number} has an empty or duplicate inspection_id')
            seen.add(row.inspection_id)
            try:
                pressure = parse_pressure_pa(row.raw_pressure)
            except ValueError as exc:
                raise MeasurementError(f'data record {row.record_number} ({row.inspection_id}): {exc}') from exc
            details.append(MeasurementDetail(row, pressure, pa_to_kpa(pressure)))
    except RowFormatError as exc:
        raise MeasurementError(str(exc)) from exc
    return summarize_batch(details)
