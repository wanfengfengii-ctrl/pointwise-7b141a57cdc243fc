import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.run import initialize_workspace
from archive.store import open_archive
from capture.collect import collect_order
from capture.orders import make_order
from intake.files import read_received_file
from intake.receive import receive_material
from portal.records import load_records
from portal.render import Portal

SHIFT = '2026-11-21/night'
PORTAL = 'river-gate'


class ProjectCase(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix='river-test-')
        self.addCleanup(directory.cleanup)
        self.workspace = initialize_workspace(Path(directory.name))
        self.spool_path = self.workspace / 'capture.sqlite3'
        self.archive_path = self.workspace / 'archive.sqlite3'
        self.records = load_records(self.workspace / 'records.csv')
        self.portal = Portal(PORTAL, self.records, 'east', 1)
        self.addCleanup(self.portal.close)
        self.spool = sqlite3.connect(self.spool_path)
        self.addCleanup(lambda: self.spool.close())
        self.archive = open_archive(self.archive_path)
        self.addCleanup(lambda: self.archive.close())

    def order(self, name='order-A', position=1, region='west', quarter=2):
        return make_order(name, PORTAL, region, quarter, SHIFT, position)

    def capture(self, order, export_id, *, portal=None, drive=None, before_restore=None):
        source = self.portal if portal is None else portal
        result = collect_order(self.spool, source, order, export_id, drive, before_restore=before_restore)
        self.assertEqual(result.status, 'ready', result.code)
        self.assertIsNotNone(result.delivery)
        return result.delivery

    def reopen_archive(self):
        self.archive.close()
        self.archive = open_archive(self.archive_path)

    def file(self, delivery):
        evidence = delivery.evidence
        return read_received_file(evidence.order_id, evidence.portal_id, evidence.export_id, delivery.content)

    def accept(self, delivery):
        file = self.file(delivery)
        pending = receive_material(self.archive, delivery.order, file.export_id, file)
        self.assertEqual(pending.status, 'pending')
        self.assertIsNone(pending.receipt)
        return receive_material(self.archive, delivery.order, file.export_id, delivery.evidence)
