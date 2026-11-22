from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.history import ArchivedResult, get_archive
from archive.shift import shift_manifest
from archive.store import open_archive
from capture.collect import collect_order
from capture.orders import CaptureOrder, make_order
from capture.spool import ensure_delivery_schema, load_delivery
from intake.files import read_received_file
from intake.receive import receive_material
from intake.store import ensure_intake_schema
from portal.records import load_records, require_identifier
from portal.render import Portal, render_export
from portal.view import edit_filter, validate_filter

SAMPLES = Path(__file__).resolve().parents[1] / 'samples'


@dataclass(frozen=True)
class RecordedJob:
    order: CaptureOrder
    export_id: str
    first: str
    events: tuple[tuple, ...]

    def drive(self, portal: Portal, export_id: str) -> None:
        for action in self.events:
            if action[0] == 'render':
                render_export(portal, export_id)
            else:
                edit_filter(portal.view, action[1], action[2], 'duty-operator')


def _validate_script(events: list) -> tuple[tuple, ...]:
    actions = []
    rendered = False
    for action in events:
        if action == ['render'] and not rendered:
            rendered = True
        elif isinstance(action, list) and len(action) == 3 and action[0] == 'edit':
            validate_filter(action[1], action[2])
        else:
            raise ValueError(f'invalid recorded action: {action!r}')
        actions.append(tuple(action))
    if not rendered:
        raise ValueError('every demo export needs a render action')
    return tuple(actions)


def _read_day(workspace: Path) -> tuple[Portal, tuple[RecordedJob, ...]]:
    specification = json.loads((workspace / 'day.json').read_text(encoding='utf-8'))
    portal_id = specification['portal_id']
    initial = specification['initial']
    records = load_records(workspace / 'records.csv')
    jobs = []
    orders, exports, positions = set(), set(), set()
    for job in specification['jobs']:
        order = make_order(job['order_id'], portal_id, job['region'], job['quarter'], job['shift'], job['position'])
        export_id = job['export_id']
        require_identifier(export_id, 'export_id')
        position = order.shift, order.position
        if order.order_id in orders or export_id in exports or position in positions:
            raise ValueError('duplicate order, export, or shift position in day recording')
        if job['first'] not in ('file', 'evidence'):
            raise ValueError('first material must be file or evidence')
        jobs.append(RecordedJob(order, export_id, job['first'], _validate_script(job['events'])))
        orders.add(order.order_id)
        exports.add(export_id)
        positions.add(position)
    return Portal(portal_id, records, initial['region'], initial['quarter']), tuple(jobs)


def initialize_workspace(workspace: str | Path) -> Path:
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    for name in ('records.csv', 'day.json'):
        destination = workspace / name
        if destination.exists():
            continue
        source = (SAMPLES / name).read_bytes()
        try:
            with destination.open('xb') as target:
                target.write(source)
        except FileExistsError:
            continue
    with closing(sqlite3.connect(workspace / 'capture.sqlite3')) as spool:
        ensure_delivery_schema(spool)
    with closing(open_archive(workspace / 'archive.sqlite3')) as archive:
        ensure_intake_schema(archive)
    return workspace


def _archive_payload(result: ArchivedResult) -> dict:
    payload = asdict(result)
    for row, detail in zip(payload['rows'], result.rows):
        if detail.pressure_pa is not None:
            row['pressure_pa'] = format(Decimal(detail.pressure_pa), 'f')
            row['pressure_kpa'] = format(detail.pressure_kpa, 'f')
    return payload


def run_day(workspace: str | Path) -> dict:
    workspace = initialize_workspace(workspace)
    portal, jobs = _read_day(workspace)
    capture_path = workspace / 'capture.sqlite3'
    try:
        with closing(sqlite3.connect(capture_path)) as spool:
            for job in jobs:
                result = collect_order(spool, portal, job.order, job.export_id, job.drive)
                if result.status != 'ready':
                    raise RuntimeError(f'capture {job.order.order_id}: {result.code}')
    finally:
        portal.close()

    archive_path = workspace / 'archive.sqlite3'
    intake = []
    with closing(sqlite3.connect(capture_path)) as spool, closing(open_archive(archive_path)) as archive:
        for job in jobs:
            order = job.order
            delivery = load_delivery(spool, order.portal_id, job.export_id)
            file = read_received_file(order.order_id, order.portal_id, job.export_id, delivery.content)
            materials = (file, delivery.evidence) if job.first == 'file' else (delivery.evidence, file)
            for piece in materials:
                result = receive_material(archive, order, job.export_id, piece)
            if result.status == 'failed':
                raise RuntimeError(f'intake {order.order_id}: {result.code}')
            intake.append({'order_id': order.order_id, 'status': result.status, 'code': result.code})

    handoff = {}
    with closing(open_archive(archive_path)) as archive:
        for shift in sorted({job.order.shift for job in jobs}):
            entries = shift_manifest(archive, shift)
            handoff[shift] = [_archive_payload(get_archive(archive, entry.result_id)) for entry in entries]
    return {'intake': intake, 'handoff': handoff}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='River inspection shift archive')
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init')
    init.add_argument('workspace', type=Path)
    demo = commands.add_parser('demo')
    demo.add_argument('--workspace', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            initialize_workspace(args.workspace)
            return 0
        if args.workspace is None:
            with TemporaryDirectory(prefix='river-inspection-') as work:
                result = run_day(work)
        else:
            result = run_day(args.workspace)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, csv_error, sqlite3.Error, KeyError, TypeError, ValueError, RuntimeError) as exc:
        parser.exit(1, f'{exc}\n')
    return 0


from csv import Error as csv_error

if __name__ == '__main__':
    raise SystemExit(main())
