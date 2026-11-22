import json
import sqlite3
from dataclasses import asdict, replace
from decimal import Decimal

from archive.history import get_archive
from archive.shift import shift_manifest
from capture.spool import load_delivery
from intake.receive import receive_material
from portal.render import render_export
from portal.view import edit_filter, read_filter
from tests import PORTAL, SHIFT, ProjectCase


class IntakeTests(ProjectCase):
    def test_actual_render_context(self):
        order = self.order()

        def wrong_order(portal, export_id):
            edit_filter(portal.view, 'quarter', 3, 'operator')
            render_export(portal, export_id)
            edit_filter(portal.view, 'quarter', 2, 'operator')

        def right_order(portal, export_id):
            render_export(portal, export_id)
            edit_filter(portal.view, 'quarter', 3, 'operator')
            edit_filter(portal.view, 'quarter', 2, 'operator')

        wrong = self.capture(order, 'actual-q3', drive=wrong_order)
        self.assertEqual(read_filter(self.portal.view, 'quarter').value, 2)
        right = self.capture(order, 'actual-q2', drive=right_order)
        self.assertEqual(read_filter(self.portal.view, 'quarter').value, 2)
        self.assertEqual(wrong.content, right.content)
        rejection = self.accept(wrong)
        outcome = (rejection.status, rejection.code, rejection.receipt)
        self.assertEqual(outcome, ('rejected', 'EXPORT_CONTEXT_MISMATCH', None), 'EXPORT_CONTEXT_MISMATCH')
        self.assertEqual(shift_manifest(self.archive, SHIFT), (), 'EXPORT_CONTEXT_MISMATCH')
        self.assertEqual((wrong.evidence.quarter, right.evidence.quarter), (3, 2), 'EXPORT_CONTEXT_MISMATCH')
        accepted = self.accept(right)
        self.assertEqual(accepted.status, 'accepted', 'EXPORT_CONTEXT_MISMATCH')
        result = get_archive(self.archive, accepted.result_id)
        self.assertEqual(result.evidence, right.evidence)
        self.assertEqual(result.receipt, accepted.receipt)
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'v3.same_bytes_render_order',
            'input': {'request_quarter': 2, 'download_quarter': 2, 'same_bytes': wrong.content == right.content},
            'expected': ['rejected', 'accepted'], 'actual': [rejection.status, accepted.status],
            'downstream_expected': {'export_id': 'actual-q2', 'quarter': 2},
            'downstream_actual': {'export_id': result.evidence.export_id, 'quarter': result.evidence.quarter},
            'code_paths': ['portal/render.py', 'capture/evidence.py', 'intake/eligibility.py', 'archive/history.py'],
            'oracle': 'Read the quarter at the scheduled render action in each independently ordered script.',
        }))

    def test_render_region_mismatch(self):
        def render_east(portal, export_id):
            edit_filter(portal.view, 'region', 'east', 'operator')
            render_export(portal, export_id)

        delivery = self.capture(self.order(), 'east-not-west', drive=render_east)
        self.assertEqual((delivery.evidence.region, delivery.evidence.quarter), ('east', 2))
        result = self.accept(delivery)
        self.assertEqual((result.status, result.code, result.receipt), ('rejected', 'EXPORT_CONTEXT_MISMATCH', None))
        self.assertEqual(shift_manifest(self.archive, SHIFT), ())

    def test_reject_other_export_context(self):
        order = self.order()
        a = self.capture(order, 'export-A')
        b = self.capture(order, 'export-B')
        self.assertEqual(a.content, b.content)
        self.assertEqual(a.evidence.file_digest, b.evidence.file_digest)
        file = self.file(a)
        self.assertEqual(receive_material(self.archive, order, 'export-A', file).status, 'pending')
        other = receive_material(self.archive, order, 'export-A', b.evidence)
        self.assertEqual((other.status, other.code, other.receipt), ('rejected', 'MATERIAL_IDENTITY_MISMATCH', None))
        self.assertEqual(receive_material(self.archive, order, 'export-B', b.evidence).status, 'pending')
        bad_digest = replace(a.evidence, file_digest='0' * 64)
        self.assertEqual(receive_material(self.archive, order, 'export-A', bad_digest).code, 'FILE_DIGEST_MISMATCH')
        self.assertEqual(receive_material(self.archive, order, 'export-A').status, 'pending')
        self.assertEqual(shift_manifest(self.archive, SHIFT), ())
        accepted = receive_material(self.archive, order, 'export-A', asdict(a.evidence))
        self.assertEqual(accepted.status, 'accepted')
        self.assertEqual(get_archive(self.archive, accepted.result_id).evidence, a.evidence)
        self.assertEqual(receive_material(self.archive, order, 'export-B', self.file(b)).code, 'ORDER_ALREADY_ARCHIVED')

    def test_accept_after_portal_close(self):
        first, second = self.order(), self.order('evidence-first', 2, 'east', 1)
        a = self.capture(first, 'file-first')
        b = self.capture(second, 'evidence-first')
        first_response = receive_material(self.archive, first, 'file-first', self.file(a))
        self.assertEqual((first_response.status, first_response.receipt), ('pending', None))
        evidence_response = receive_material(self.archive, second, 'evidence-first', b.evidence)
        self.assertEqual((evidence_response.status, evidence_response.receipt), ('pending', None))
        self.reopen_archive()
        self.assertEqual(receive_material(self.archive, first, 'file-first').status, 'pending')
        self.assertEqual(receive_material(self.archive, second, 'evidence-first').status, 'pending')
        damaged = replace(self.file(b), content=b.content + b'\r\n')
        self.assertEqual(receive_material(self.archive, second, 'evidence-first', damaged).code, 'FILE_DIGEST_MISMATCH')
        self.assertEqual(receive_material(self.archive, first, 'file-first', a.evidence, process=False).code, 'DEFERRED')
        self.assertEqual(shift_manifest(self.archive, SHIFT), ())
        self.portal.close()
        self.spool.close()
        self.spool = sqlite3.connect(self.spool_path)
        self.reopen_archive()
        offline = load_delivery(self.spool, PORTAL, 'evidence-first')
        self.assertEqual(offline, b)
        second_result = receive_material(self.archive, second, 'evidence-first', self.file(offline))
        self.assertEqual(second_result.status, 'accepted')
        succeeded = receive_material(self.archive, first, 'file-first')
        self.assertEqual(succeeded.status, 'accepted')
        result = get_archive(self.archive, succeeded.result_id)
        self.assertEqual(result.evidence, a.evidence)
        self.assertEqual(result.receipt, succeeded.receipt)
        self.assertEqual([row.pressure_pa for row in result.rows], [1250, None])
        self.assertEqual([row.pressure_kpa for row in result.rows], [Decimal('1.25'), None])
        self.assertEqual(result.missing_count, 1)
        self.assertEqual([row.order_id for row in shift_manifest(self.archive, SHIFT)], [first.order_id, second.order_id])
        self.assertEqual(receive_material(self.archive, first, 'file-first', self.file(a)).receipt, succeeded.receipt)
