import sqlite3

from archive.history import get_archive
from archive.shift import shift_manifest
from capture.collect import collect_order, recover_borrow
from capture.spool import ensure_delivery_schema, load_borrow, load_delivery
from intake.files import read_received_file
from intake.receive import receive_material
from portal.ledger import BorrowSettledError
from portal.render import Portal
from portal.view import edit_filter, read_filter
from portal.wire import CaptureLinkLost, ScriptedLink
from tests import PORTAL, SHIFT, ProjectCase


class BorrowRecoveryTests(ProjectCase):
    def reopen_spool(self):
        self.spool.close()
        connection = sqlite3.connect(self.spool_path)
        ensure_delivery_schema(connection)
        self.spool = connection
        return self.spool

    def cut_borrow(self, field, mode, borrow_id, export_id='cut', order=None):
        link = ScriptedLink()
        link.arm('borrow', field, mode)
        order = self.order() if order is None else order
        with self.assertRaises(CaptureLinkLost):
            collect_order(
                self.spool, self.portal, order, export_id,
                borrow_id=borrow_id, send_edit=link.send_edit,
            )
        return link

    def cut_restore(self, field, borrow_id, link=None):
        link = ScriptedLink() if link is None else link
        link.arm('restore', field, 'drop_reply')
        with self.assertRaises(CaptureLinkLost):
            recover_borrow(self.spool, self.portal, borrow_id, send_edit=link.send_edit)
        return link

    # -- timing one: region borrow drops its reply ------------------------

    def test_drop_reply_region_is_restored_after_reopen(self):
        link = self.cut_borrow('region', 'drop_reply', 'brw-drop-1', 'exp-drop-1')
        written = read_filter(self.portal.view, 'region')
        self.assertEqual((written.value, written.actor, written.version),
                         ('west', 'capture/borrow/brw-drop-1', 1))
        self.assertEqual(load_borrow(self.spool, 'brw-drop-1')['status'], 'open')

        self.reopen_spool()
        self.assertEqual(recover_borrow(self.spool, self.portal, 'brw-drop-1'), 'settled')
        restored = read_filter(self.portal.view, 'region')
        self.assertEqual((restored.value, restored.actor), ('east', 'capture/borrow/brw-drop-1'))
        self.assertEqual(restored.version, written.version + 1)
        self.assertEqual(load_borrow(self.spool, 'brw-drop-1')['status'], 'settled')

        # Late or duplicate commands of the settled attempt change nothing.
        before = read_filter(self.portal.view, 'region')
        with self.assertRaises(BorrowSettledError):
            link.deliver()
        with self.assertRaises(BorrowSettledError):
            link.deliver()
        self.assertEqual(read_filter(self.portal.view, 'region'), before)
        # Repeated recovery and unknown id.
        self.assertEqual(recover_borrow(self.spool, self.portal, 'brw-drop-1'), 'settled')
        self.assertEqual(recover_borrow(self.spool, self.portal, 'brw-missing'), 'unknown')

    def test_human_same_value_reselection_is_kept(self):
        self.cut_borrow('region', 'drop_reply', 'brw-human-1', 'exp-human-1')
        human = edit_filter(self.portal.view, 'region', 'west', 'operator')

        self.reopen_spool()
        self.assertEqual(recover_borrow(self.spool, self.portal, 'brw-human-1'), 'settled')
        self.assertEqual(read_filter(self.portal.view, 'region'), human)

    def test_held_borrow_that_never_ran_has_no_restore_write(self):
        edit_filter(self.portal.view, 'region', 'east', 'operator')
        link = self.cut_borrow('region', 'hold', 'brw-hold-1', 'exp-hold-1')
        before = read_filter(self.portal.view, 'region')

        self.reopen_spool()
        self.assertEqual(recover_borrow(self.spool, self.portal, 'brw-hold-1'), 'settled')
        self.assertEqual(read_filter(self.portal.view, 'region'), before)
        # Delivering the stale command now is refused and leaves the view alone.
        with self.assertRaises(BorrowSettledError):
            link.deliver()
        self.assertEqual(read_filter(self.portal.view, 'region'), before)

    # -- timing two: region succeeds, quarter command is retained ---------

    def test_held_quarter_delivered_before_settle_is_returned_by_ownership(self):
        link = self.cut_borrow('quarter', 'hold', 'brw-quarter-1', 'exp-quarter-1')
        self.assertEqual(read_filter(self.portal.view, 'region').value, 'west')
        self.assertEqual(read_filter(self.portal.view, 'quarter').value, 1)

        # While open, a new export of the same view is busy; a human may edit.
        busy = collect_order(self.spool, self.portal, self.order('order-busy', 2),
                             'exp-busy', borrow_id='brw-busy')
        self.assertEqual((busy.status, busy.delivery, busy.code), ('busy', None, None))
        human = edit_filter(self.portal.view, 'quarter', 3, 'operator')
        edit_filter(self.portal.view, 'quarter', 1, 'operator')
        # The retained command carries the stale expected version and is rejected.
        with self.assertRaises(Exception) as caught:
            link.deliver()
        self.assertEqual(caught.exception.code, 'FILTER_VERSION_CONFLICT')
        self.assertEqual(read_filter(self.portal.view, 'quarter').actor, 'operator')

        self.reopen_spool()
        self.assertEqual(recover_borrow(self.spool, self.portal, 'brw-quarter-1'), 'settled')
        region = read_filter(self.portal.view, 'region')
        quarter = read_filter(self.portal.view, 'quarter')
        self.assertEqual((region.value, region.actor), ('east', 'capture/borrow/brw-quarter-1'))
        # Human still owns quarter, so no restore write touched it.
        self.assertEqual(quarter, read_filter(self.portal.view, 'quarter'))
        self.assertEqual(quarter.actor, 'operator')

    def test_held_quarter_delivered_after_settle_cannot_write(self):
        link = self.cut_borrow('quarter', 'hold', 'brw-quarter-2', 'exp-quarter-2')
        held_before = read_filter(self.portal.view, 'quarter')

        self.reopen_spool()
        self.assertEqual(recover_borrow(self.spool, self.portal, 'brw-quarter-2'), 'settled')
        self.assertEqual(read_filter(self.portal.view, 'region').value, 'east')
        self.assertEqual(read_filter(self.portal.view, 'quarter'), held_before)

        with self.assertRaises(BorrowSettledError):
            link.deliver()
        after = read_filter(self.portal.view, 'quarter')
        self.assertEqual(after, held_before)

    def test_held_quarter_that_lands_before_settle_is_restored(self):
        link = self.cut_borrow('quarter', 'hold', 'brw-quarter-3', 'exp-quarter-3')
        # No human edit: the retained command still matches the planned version.
        landed = link.deliver()
        self.assertEqual((landed.value, landed.actor, landed.version),
                         (2, 'capture/borrow/brw-quarter-3', 1))

        self.reopen_spool()
        self.assertEqual(recover_borrow(self.spool, self.portal, 'brw-quarter-3'), 'settled')
        quarter = read_filter(self.portal.view, 'quarter')
        self.assertEqual((quarter.value, quarter.actor), (1, 'capture/borrow/brw-quarter-3'))
        self.assertEqual(read_filter(self.portal.view, 'region').value, 'east')
        # The now-settled retained command can no longer change anything.
        settled_version = quarter.version
        with self.assertRaises(BorrowSettledError):
            link.deliver()
        self.assertEqual(read_filter(self.portal.view, 'quarter').version, settled_version)

    # -- restore reply lost: reopen and continue --------------------------

    def test_restore_drop_reply_is_idempotent(self):
        link = self.cut_borrow('region', 'drop_reply', 'brw-restore-1', 'exp-restore-1')
        self.reopen_spool()
        restore_link = self.cut_restore('region', 'brw-restore-1')
        once = read_filter(self.portal.view, 'region')
        self.assertEqual((once.value, once.actor), ('east', 'capture/borrow/brw-restore-1'))
        self.assertEqual(load_borrow(self.spool, 'brw-restore-1')['status'], 'open')

        self.reopen_spool()
        self.assertEqual(recover_borrow(self.spool, self.portal, 'brw-restore-1'), 'settled')
        # No additional restore write happened on the second run.
        self.assertEqual(read_filter(self.portal.view, 'region'), once)
        # Re-delivering the dropped borrow command and the dropped restore command.
        with self.assertRaises(BorrowSettledError):
            link.deliver()
        with self.assertRaises(BorrowSettledError):
            restore_link.deliver()
        self.assertEqual(read_filter(self.portal.view, 'region'), once)

    # -- session identity and busy semantics ------------------------------

    def test_new_portal_session_cannot_claim_old_attempt(self):
        self.cut_borrow('region', 'drop_reply', 'brw-session-1', 'exp-session-1')
        self.reopen_spool()
        new_portal = Portal(PORTAL, self.records, 'east', 1)
        self.addCleanup(new_portal.close)
        self.assertEqual(recover_borrow(self.spool, new_portal, 'brw-session-1'), 'unknown')
        # The original session still settles its own attempt.
        self.assertEqual(recover_borrow(self.spool, self.portal, 'brw-session-1'), 'settled')

    def test_open_borrow_blocks_new_collect_but_settled_id_fails_fast(self):
        self.cut_borrow('region', 'drop_reply', 'brw-open-1', 'exp-open-1')
        blocked = collect_order(self.spool, self.portal, self.order('order-other', 2),
                                'exp-other', borrow_id='brw-open-2')
        self.assertEqual((blocked.status, blocked.delivery), ('busy', None))
        reused = collect_order(self.spool, self.portal, self.order(), 'exp-reuse',
                               borrow_id='brw-open-1')
        self.assertEqual((reused.status, reused.code), ('failed', 'BORROW_ALREADY_OPEN'))
        # Human edits remain available while the borrow is unresolved.
        human = edit_filter(self.portal.view, 'region', 'north', 'operator')
        self.assertEqual(read_filter(self.portal.view, 'region'), human)

        self.reopen_spool()
        self.assertEqual(recover_borrow(self.spool, self.portal, 'brw-open-1'), 'settled')
        settled_again = collect_order(self.spool, self.portal, self.order(), 'exp-settled',
                                      borrow_id='brw-open-1')
        self.assertEqual((settled_again.status, settled_again.code), ('failed', 'BORROW_SETTLED'))

    def test_recover_busy_when_restore_keeps_losing(self):
        self.cut_borrow('region', 'drop_reply', 'brw-busy-1', 'exp-busy-1')
        self.reopen_spool()
        flaky = ScriptedLink()
        flaky.arm('restore', 'region', 'drop_reply')
        with self.assertRaises(CaptureLinkLost):
            recover_borrow(self.spool, self.portal, 'brw-busy-1', send_edit=flaky.send_edit)
        self.assertEqual(load_borrow(self.spool, 'brw-busy-1')['status'], 'open')
        # A plain retry, with no new interruption, settles the borrow.
        self.reopen_spool()
        self.assertEqual(recover_borrow(self.spool, self.portal, 'brw-busy-1'), 'settled')

    # -- next order after settlement, plus preserved material -------------

    def test_next_order_and_reclaim_after_settlement(self):
        self.cut_borrow('region', 'drop_reply', 'brw-next-1', 'exp-next-1')
        self.reopen_spool()
        self.assertEqual(recover_borrow(self.spool, self.portal, 'brw-next-1'), 'settled')

        order = self.order('order-next', 2)
        result = collect_order(self.spool, self.portal, order, 'exp-next-2',
                               borrow_id='brw-next-2')
        self.assertEqual(result.status, 'ready')
        self.assertEqual((result.delivery.evidence.region, result.delivery.evidence.quarter),
                         ('west', 2))
        self.assertEqual(read_filter(self.portal.view, 'region').value, 'east')
        # The new borrow settled itself, and its delivery is reclaimable by id.
        self.assertEqual(load_borrow(self.spool, 'brw-next-2')['status'], 'settled')
        self.assertEqual(load_delivery(self.spool, PORTAL, 'exp-next-2'), result.delivery)
        reclaimed = collect_order(self.spool, self.portal, order, 'exp-next-2')
        self.assertEqual((reclaimed.status, reclaimed.delivery), ('ready', result.delivery))

    def test_preserved_material_and_signed_archive_claimable_through_cut(self):
        # A fully captured, signed order from before the interruption.
        signed = self.capture(self.order('order-signed', 3), 'exp-signed')
        accepted = self.accept(signed)
        self.assertEqual(accepted.status, 'accepted')
        result_id = accepted.result_id

        # Interrupt a later borrow, settle it, and claim both kinds of assets.
        self.cut_borrow('region', 'drop_reply', 'brw-claim-1', 'exp-claim-1')
        self.reopen_spool()
        self.assertEqual(recover_borrow(self.spool, self.portal, 'brw-claim-1'), 'settled')

        delivery = load_delivery(self.spool, PORTAL, 'exp-signed')
        self.assertEqual(delivery, signed)
        file = read_received_file(signed.evidence.order_id, PORTAL, 'exp-signed', delivery.content)
        replay = receive_material(self.archive, signed.order, 'exp-signed', file)
        self.assertEqual((replay.status, replay.result_id), ('accepted', result_id))
        detail = get_archive(self.archive, result_id)
        self.assertIsNotNone(detail)
        self.assertEqual(detail.evidence.export_id, 'exp-signed')
        manifest = shift_manifest(self.archive, SHIFT)
        self.assertIn(result_id, [entry.result_id for entry in manifest])
