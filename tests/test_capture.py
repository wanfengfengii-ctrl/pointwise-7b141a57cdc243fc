from capture.collect import collect_order
from capture.orders import make_order
from capture.spool import load_delivery
from portal.download import render_download
from portal.events import FilterWrite, PortalError, RenderEvent
from portal.render import Portal, render_export, request_export
from portal.view import VersionConflict, edit_filter, read_filter
from tests import PORTAL, SHIFT, ProjectCase


class CaptureTests(ProjectCase):
    def test_collect_and_restore(self):
        order = self.order()
        delivery = self.capture(order, 'normal')
        evidence = delivery.evidence
        self.assertEqual((evidence.order_id, evidence.portal_id, evidence.export_id), ('order-A', PORTAL, 'normal'))
        self.assertEqual((evidence.region, evidence.quarter), ('west', 2))
        self.assertEqual(delivery.content, b'inspection_id,pressure_pa\r\nW-inlet,1250\r\nW-outlet,\r\n')
        history = self.portal.events.events_through(evidence.render_sequence)
        self.assertIsInstance(history[-1], RenderEvent)
        self.assertEqual(history[-1].export_id, 'normal')
        self.assertEqual(history[-1].file_digest, evidence.file_digest)
        self.assertEqual(load_delivery(self.spool, PORTAL, 'normal'), delivery)
        self.assertEqual((read_filter(self.portal.view, 'region').value, read_filter(self.portal.view, 'quarter').value), ('east', 1))
        self.assertEqual((read_filter(self.portal.view, 'region').version, read_filter(self.portal.view, 'quarter').version), (2, 2))
        self.assertEqual(collect_order(self.spool, self.portal, order, 'normal').delivery, delivery)
        self.assertEqual(collect_order(self.spool, self.portal, self.order('other', 2), 'normal').code, 'CAPTURE_BINDING_CONFLICT')

    def test_download_pending_and_identity(self):
        request_export(self.portal, 'pending')
        self.assertIsNone(render_download(self.portal, 'pending'))
        with self.assertRaisesRegex(PortalError, 'UNKNOWN_EXPORT'):
            render_download(self.portal, 'unknown')
        rendered = render_export(self.portal, 'pending')
        artifact = render_download(self.portal, 'pending')
        self.assertEqual((artifact.render_sequence, artifact.content), (rendered.render_sequence, rendered.content))
        with self.assertRaisesRegex(PortalError, 'EXPORT_ALREADY_RENDERED'):
            render_export(self.portal, 'pending')
        with self.assertRaisesRegex(PortalError, 'DUPLICATE_EXPORT'):
            request_export(self.portal, 'pending')
        self.assertEqual(render_download(self.portal, 'pending'), artifact)
        self.portal.make_unavailable('pending')
        with self.assertRaisesRegex(PortalError, 'EXPORT_UNAVAILABLE'):
            render_download(self.portal, 'pending')
        self.portal.close()
        with self.assertRaisesRegex(PortalError, 'PORTAL_CLOSED'):
            render_download(self.portal, 'pending')

    def test_take_failure_and_preservation_failure(self):
        def leave_render_queued(portal, export_id):
            self.assertIsNone(render_download(portal, export_id))

        order = self.order()
        pending = collect_order(self.spool, self.portal, order, 'queued', leave_render_queued)
        self.assertEqual((pending.status, pending.code, pending.delivery), ('failed', 'EXPORT_PENDING', None))
        self.assertIsNone(load_delivery(self.spool, PORTAL, 'queued'))
        self.assertEqual((read_filter(self.portal.view, 'region').value, read_filter(self.portal.view, 'quarter').value), ('east', 1))

        def make_unavailable(portal, export_id):
            render_export(portal, export_id)
            edit_filter(portal.view, 'region', 'west', 'operator')
            portal.make_unavailable(export_id)

        failed = collect_order(self.spool, self.portal, order, 'blocked', make_unavailable)
        self.assertEqual((failed.status, failed.code), ('failed', 'EXPORT_UNAVAILABLE'))
        self.assertIsNone(load_delivery(self.spool, PORTAL, 'blocked'))
        self.assertEqual(read_filter(self.portal.view, 'region').actor, 'operator')
        self.assertEqual(read_filter(self.portal.view, 'region').value, 'west')
        self.assertEqual(read_filter(self.portal.view, 'quarter').value, 1)

        original = self.capture(order, 'kept')
        portal = Portal(PORTAL, self.records, 'east', 1)
        self.addCleanup(portal.close)
        self.spool.execute('PRAGMA query_only = ON')
        failed = collect_order(self.spool, portal, order, 'cannot-store')
        self.assertEqual((failed.status, failed.code, failed.delivery), ('failed', 'DELIVERY_STORAGE_FAILED', None))
        self.assertIsNone(load_delivery(self.spool, PORTAL, 'cannot-store'))
        self.assertEqual(load_delivery(self.spool, PORTAL, 'kept'), original)
        self.assertEqual((read_filter(portal.view, 'region').value, read_filter(portal.view, 'quarter').value), ('east', 1))

    def test_preserve_human_edits(self):
        def render_after_same_value(portal, export_id):
            before = read_filter(portal.view, 'region')
            human = edit_filter(portal.view, 'region', 'west', 'operator')
            self.assertEqual(human.version, before.version + 1)
            render_export(portal, export_id)

        with self.subTest('same_value'):
            delivery = self.capture(self.order(), 'same-value', drive=render_after_same_value)
            final = read_filter(self.portal.view, 'region')
            self.assertEqual((final.value, final.actor, final.version), ('west', 'operator', 2))
            self.assertEqual(read_filter(self.portal.view, 'quarter').value, 1)
            event = self.portal.events.events_through(final.sequence)[-1]
            self.assertIsInstance(event, FilterWrite)
            self.assertLess(final.sequence, delivery.evidence.render_sequence)
        with self.subTest('cleanup_race'):
            portal = Portal(PORTAL, self.records, 'east', 1)
            self.addCleanup(portal.close)
            writes = []

            def intervene(field):
                if field == 'region':
                    writes.append(edit_filter(portal.view, 'region', 'north', 'operator'))

            raced = self.capture(self.order(), 'cleanup-race', portal=portal, before_restore=intervene)
            self.assertEqual(len(writes), 1)
            self.assertEqual(read_filter(portal.view, 'region'), writes[0])
            self.assertEqual((writes[0].actor, writes[0].value), ('operator', 'north'))
            self.assertGreater(writes[0].sequence, raced.evidence.render_sequence)
            self.assertEqual(read_filter(portal.view, 'quarter').value, 1)

    def test_illegal_and_stale_writes_leave_no_event(self):
        with self.assertRaises(ValueError):
            edit_filter(self.portal.view, 'quarter', True, 'operator')
        first = edit_filter(self.portal.view, 'region', 'east', 'operator', expected_version=0)
        with self.assertRaises(VersionConflict):
            edit_filter(self.portal.view, 'region', 'south', 'operator', expected_version=0)
        self.assertEqual(read_filter(self.portal.view, 'region'), first)
        self.assertEqual(first.sequence, 1)
        self.assertEqual(edit_filter(self.portal.view, 'quarter', 4, 'operator').sequence, 2)
        with self.assertRaises(ValueError):
            make_order('bad', PORTAL, 'west', 0, SHIFT, 1)
