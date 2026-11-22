import sqlite3
from dataclasses import replace
from decimal import Decimal

from archive.history import get_archive
from archive.run import initialize_workspace, run_day
from archive.shift import shift_manifest
from intake.receive import receive_material
from portal.render import render_export
from portal.view import edit_filter
from tests import SHIFT, ProjectCase


class ArchiveTests(ProjectCase):
    def test_publish_failure_and_retry(self):
        order = self.order()
        delivery = self.capture(order, 'commit-case')
        file = self.file(delivery)
        self.assertEqual(receive_material(self.archive, order, file.export_id, file).status, 'pending')
        staged = receive_material(self.archive, order, file.export_id, delivery.evidence, process=False)
        self.assertEqual((staged.status, staged.receipt), ('pending', None))
        denied = []

        def deny_commit(operation, action, object_name, database, origin):
            if operation == sqlite3.SQLITE_TRANSACTION and action == 'COMMIT':
                denied.append(action)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        self.archive.set_authorizer(deny_commit)
        try:
            failure = receive_material(self.archive, order, file.export_id)
        finally:
            self.archive.set_authorizer(None)
        self.assertEqual(denied, ['COMMIT'])
        self.assertEqual((failure.status, failure.code, failure.result_id, failure.receipt), (
            'failed', 'PUBLICATION_FAILED', None, None,
        ))
        self.assertFalse(self.archive.in_transaction)
        self.reopen_archive()
        self.assertEqual(shift_manifest(self.archive, SHIFT), ())
        self.assertIsNone(get_archive(self.archive, 'A-order-A'))
        self.assertEqual(self.archive.execute('SELECT COUNT(*) FROM archive_results').fetchone()[0], 0)
        self.assertEqual(self.archive.execute('SELECT COUNT(*) FROM result_rows').fetchone()[0], 0)
        self.assertEqual(self.archive.execute('SELECT COUNT(*) FROM receipts').fetchone()[0], 0)
        self.assertEqual(self.archive.execute('SELECT COUNT(*) FROM intake_material').fetchone()[0], 1)
        self.portal.close()
        accepted = receive_material(self.archive, order, file.export_id)
        self.assertEqual(accepted.status, 'accepted')
        first = get_archive(self.archive, accepted.result_id)
        self.assertEqual(first.evidence, delivery.evidence)
        self.assertEqual(first.receipt, accepted.receipt)
        self.reopen_archive()
        for material in (file, delivery.evidence):
            replay = receive_material(self.archive, order, file.export_id, material)
            self.assertEqual(replay, accepted)
            self.assertEqual(get_archive(self.archive, replay.result_id), first)
        self.assertEqual(self.archive.execute('SELECT COUNT(*) FROM archive_results').fetchone()[0], 1)
        self.assertEqual(self.archive.execute('SELECT COUNT(*) FROM receipts').fetchone()[0], 1)
        self.assertEqual(self.archive.execute('SELECT COUNT(*) FROM result_rows').fetchone()[0], 2)
        forged_order = replace(order, position=5)
        self.assertEqual(receive_material(self.archive, forged_order, file.export_id, file).code, 'ORDER_FACTS_CONFLICT')
        changed = replace(file, content=file.content + b'\n')
        conflict = receive_material(self.archive, order, file.export_id, changed)
        self.assertEqual((conflict.status, conflict.code, conflict.receipt), ('rejected', 'FILE_CONFLICT', None))

    def test_fixed_handoff_after_restart(self):
        later = self.capture(self.order('later', 3, 'east', 1), 'later')
        earlier = self.capture(self.order('earlier', 1), 'earlier')
        held = self.capture(self.order('waiting', 2), 'waiting')
        late_result = self.accept(later)
        early_result = self.accept(earlier)
        self.assertEqual((late_result.status, early_result.status), ('accepted', 'accepted'))
        self.assertEqual(receive_material(self.archive, held.order, 'waiting', self.file(held)).status, 'pending')
        saved = get_archive(self.archive, early_result.result_id)
        self.portal.close()
        self.reopen_archive()
        manifest = shift_manifest(self.archive, SHIFT)
        self.assertEqual([(row.position, row.order_id, row.result_id) for row in manifest], [
            (1, 'earlier', early_result.result_id), (3, 'later', late_result.result_id),
        ])
        original = get_archive(self.archive, early_result.result_id)
        self.assertEqual(original, saved)
        self.assertEqual(original.evidence, earlier.evidence)
        self.assertEqual(original.receipt, early_result.receipt)
        self.assertEqual(original.missing_count, 1)
        self.assertEqual([(d.row.record_number, d.row.inspection_id, d.pressure_pa, d.pressure_kpa) for d in original.rows], [
            (1, 'W-inlet', 1250, Decimal('1.25')), (2, 'W-outlet', None, None),
        ])
        self.assertEqual(get_archive(self.archive, late_result.result_id).receipt, late_result.receipt)
        self.assertEqual(shift_manifest(self.archive, '2026-11-22/day'), ())
        with self.assertRaises(ValueError):
            shift_manifest(self.archive, '2026-02-30/night')
        self.assertIsNone(get_archive(self.archive, 'absent'))

    def test_reinitialize_preserves_history_and_work_in_progress(self):
        initialize_workspace(self.workspace)
        self.assertEqual(shift_manifest(self.archive, SHIFT), ())
        self.assertEqual(self.archive.execute('SELECT COUNT(*) FROM intake_material').fetchone()[0], 0)
        accepted = self.accept(self.capture(self.order('saved', 1), 'saved'))
        saved = get_archive(self.archive, accepted.result_id)
        self.assertIsNotNone(saved)
        wait = self.capture(self.order('wait', 2), 'wait')
        self.assertEqual(receive_material(self.archive, wait.order, 'wait', self.file(wait)).status, 'pending')

        def drift(portal, export_id):
            edit_filter(portal.view, 'quarter', 3, 'operator')
            render_export(portal, export_id)

        wrong = self.capture(self.order('wrong', 3), 'wrong', drive=drift)
        self.assertEqual(self.accept(wrong).code, 'EXPORT_CONTEXT_MISMATCH')
        records = self.workspace / 'records.csv'
        changed_records = records.read_bytes() + b'north,4,extra,250\n'
        records.write_bytes(changed_records)
        day = self.workspace / 'day.json'
        changed_day = day.read_bytes() + b'\n'
        day.write_bytes(changed_day)
        initialize_workspace(self.workspace)
        self.reopen_archive()
        self.assertEqual(records.read_bytes(), changed_records)
        self.assertEqual(day.read_bytes(), changed_day)
        self.assertEqual(get_archive(self.archive, accepted.result_id), saved)
        self.assertEqual(receive_material(self.archive, wait.order, 'wait').status, 'pending')
        self.assertEqual(receive_material(self.archive, wrong.order, 'wrong').code, 'EXPORT_CONTEXT_MISMATCH')
        self.assertEqual([entry.order_id for entry in shift_manifest(self.archive, SHIFT)], ['saved'])
        self.assertEqual(receive_material(self.archive, wait.order, 'wait', wait.evidence).status, 'accepted')

    def test_recorded_day_and_repeat(self):
        work = self.workspace / 'recorded-day'
        report = run_day(work)
        self.assertEqual([(item['order_id'], item['status'], item['code']) for item in report['intake']], [
            ('night-02', 'accepted', None), ('night-01', 'accepted', None),
            ('night-03', 'rejected', 'EXPORT_CONTEXT_MISMATCH'),
        ])
        work_order = report['handoff'][SHIFT]
        self.assertEqual([item['order']['order_id'] for item in work_order], ['night-01', 'night-02'])
        self.assertEqual([item['missing_count'] for item in work_order], [1, 0])
        self.assertEqual([row['pressure_kpa'] for row in work_order[0]['rows']], ['1.25', None])
        self.assertEqual([row['pressure_kpa'] for row in work_order[1]['rows']], ['0', '-0.25'])
        self.assertEqual(run_day(work), report)
