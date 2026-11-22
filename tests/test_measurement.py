import json
from decimal import Decimal, localcontext

from archive.history import get_archive
from archive.shift import shift_manifest
from intake.receive import receive_material
from measurement.validation import MeasurementError, parse_measurements
from portal.records import InspectionRecord
from portal.render import Portal
from tests import PORTAL, SHIFT, ProjectCase


class MeasurementTests(ProjectCase):
    def test_exact_units_and_invalid_batch(self):
        valid = self.capture(self.order(), 'valid-pressure')
        accepted = self.accept(valid)
        self.assertEqual(accepted.status, 'accepted')
        archive = get_archive(self.archive, accepted.result_id)
        expected = [('W-inlet', '1250', 1250, '1.25'), ('W-outlet', '', None, None)]
        actual = [(item.row.inspection_id, item.row.raw_pressure, item.pressure_pa,
                   None if item.pressure_kpa is None else format(item.pressure_kpa, 'f')) for item in archive.rows]
        self.assertEqual(actual, expected)
        self.assertEqual([(row.row.portal_id, row.row.export_id, row.row.record_number) for row in archive.rows], [
            (PORTAL, 'valid-pressure', 1), (PORTAL, 'valid-pressure', 2),
        ])
        self.assertEqual(archive.missing_count, 1)
        manifest = shift_manifest(self.archive, SHIFT)
        self.assertEqual(manifest[0].missing_count, 1)

        bad_portal = Portal(PORTAL, (
            InspectionRecord('west', 2, 'good', '1250'),
            InspectionRecord('west', 2, 'bad', '12.5'),
        ), 'east', 1)
        self.addCleanup(bad_portal.close)
        invalid = self.capture(self.order('bad-pressure', 2), 'invalid-pressure', portal=bad_portal)
        rejected = self.accept(invalid)
        self.assertEqual((rejected.status, rejected.code, rejected.receipt), ('rejected', 'INVALID_MEASUREMENT_CSV', None))
        self.assertIn('data record 2 (bad)', rejected.detail)
        self.assertEqual(shift_manifest(self.archive, SHIFT), manifest)
        self.assertIsNone(get_archive(self.archive, 'A-bad-pressure'))
        self.assertEqual(receive_material(self.archive, invalid.order, 'invalid-pressure').code, 'INVALID_MEASUREMENT_CSV')
        self.assertEqual(self.archive.execute('SELECT COUNT(*) FROM result_rows').fetchone()[0], 2)
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'v6', 'input': {'original_fields': ['1250', ''], 'invalid_second_value': '12.5'},
            'expected': expected, 'actual': actual,
            'downstream_expected': {'missing': 1, 'invalid_status': 'rejected', 'formal_results': 1},
            'downstream_actual': {'missing': manifest[0].missing_count, 'invalid_status': rejected.status,
                                  'formal_results': len(shift_manifest(self.archive, SHIFT))},
            'code_paths': ['measurement/validation.py', 'measurement/unitsa.py', 'archive/history.py', 'archive/shift.py'],
            'oracle': 'Move the decimal point three places left and count only the empty field as missing.',
        }))

    def test_exact_scale_missing_and_zero_after_publication(self):
        digits = '123456789012345678901234567890123'
        source = ('+0', '', '-1', digits, '+001000')
        records = tuple(InspectionRecord('west', 2, f'probe-{index}', value) for index, value in enumerate(source))
        portal = Portal(PORTAL, records, 'east', 1)
        self.addCleanup(portal.close)
        delivery = self.capture(self.order(), 'precision', portal=portal)
        with localcontext() as context:
            context.prec = 5
            accepted = self.accept(delivery)
            self.assertEqual(accepted.status, 'accepted')
            self.reopen_archive()
            saved = get_archive(self.archive, accepted.result_id)
        self.assertEqual(tuple(row.row.raw_pressure for row in saved.rows), source)
        self.assertEqual([row.pressure_pa for row in saved.rows], [0, None, -1, 123456789012345678901234567890123, 1000])
        self.assertEqual([row.pressure_kpa for row in saved.rows], [
            Decimal('0'), None, Decimal('-0.001'), Decimal('123456789012345678901234567890.123'), Decimal('1'),
        ])
        self.assertEqual(saved.missing_count, 1)

    def test_row_identity_and_csv_structure(self):
        header = b'inspection_id,pressure_pa\n'
        malformed = {
            'duplicate': header + b'A,1250\nA,0\n',
            'blank identity': header + b',0\n',
            'extra field': header + b'A,0,x\n',
            'blank record': header + b'\n',
            'spaces are not missing': header + b'A, \n',
            'partial numeric': header + b'A,+\n',
            'unicode digits': header + 'A,１２\n'.encode('utf-8'),
            'invalid UTF-8': header + b'A,\xff\n',
            'unterminated quoting': header + b'A,"0\n',
            'incorrect header': b'pressure_pa,inspection_id\n1,A\n',
        }
        for label, payload in malformed.items():
            with self.subTest(label=label), self.assertRaises(MeasurementError):
                parse_measurements(payload, PORTAL, 'csv-validation')
        batch = parse_measurements(header + b'"A\nloop",1000\nB,\n', PORTAL, 'row-locations')
        self.assertEqual([row.row.record_number for row in batch.details], [1, 2])
        self.assertEqual([row.row.inspection_id for row in batch.details], ['A\nloop', 'B'])
        self.assertEqual(batch.missing_count, 1)
        empty = parse_measurements(header, PORTAL, 'empty-scope')
        self.assertEqual((empty.details, empty.missing_count), ((), 0))
