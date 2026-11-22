"""Runnable demonstration of durable shared-filter borrow settlement.

Run from the repository root with ``python3 -B -m borrow_demo``.  The script
uses the same ``ScriptedLink`` fixture as the tests to stage the two link-loss
timings from ``BORROW.md``, reopens the capture spool between attempts, and
shows that late commands cannot touch a settled filter before a fresh order
completes a normal pickup.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.run import initialize_workspace
from capture.collect import collect_order, recover_borrow
from capture.orders import make_order
from capture.spool import ensure_delivery_schema, load_delivery
from portal.ledger import BorrowSettledError
from portal.records import load_records
from portal.render import Portal
from portal.view import edit_filter, read_filter
from portal.wire import CaptureLinkLost, ScriptedLink

PORTAL = 'river-gate'
SHIFT = '2026-11-21/night'
ACTOR_TEMPLATE = 'capture/borrow/{}'


def _snapshot(portal) -> dict:
    return {
        field: {
            'value': read_filter(portal.view, field).value,
            'actor': read_filter(portal.view, field).actor,
            'version': read_filter(portal.view, field).version,
        }
        for field in ('region', 'quarter')
    }


def _open_spool(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    ensure_delivery_schema(connection)
    return connection


def _cut(portal, spool, order, export_id, borrow_id, phase_field, mode):
    link = ScriptedLink()
    link.arm('borrow', phase_field, mode)
    try:
        collect_order(spool, portal, order, export_id,
                      borrow_id=borrow_id, send_edit=link.send_edit)
    except CaptureLinkLost:
        print(f'  * link lost on borrow:{phase_field} ({mode}); collector exits, portal stays up')
    return link


def main() -> int:
    summary: dict = {'steps': []}
    with TemporaryDirectory(prefix='river-borrow-demo-') as work:
        workspace = initialize_workspace(Path(work))
        records = load_records(workspace / 'records.csv')
        spool_path = workspace / 'capture.sqlite3'
        portal = Portal(PORTAL, records, 'east', 1)
        order = make_order('night-borrow', PORTAL, 'west', 2, SHIFT, 1)
        next_order = make_order('night-followup', PORTAL, 'west', 2, SHIFT, 2)

        # Timing one: region borrow executes but its reply is dropped.
        print('1) region borrow drop_reply')
        spool = _open_spool(spool_path)
        link_one = _cut(portal, spool, order, 'export-one', 'borrow-1', 'region', 'drop_reply')
        print('   view at cut:', json.dumps(_snapshot(portal), ensure_ascii=False))
        spool.close()

        spool = _open_spool(spool_path)
        status = recover_borrow(spool, portal, 'borrow-1')
        print(f'   recover_borrow -> {status}')
        restored = _snapshot(portal)
        print('   view after settlement:', json.dumps(restored, ensure_ascii=False))
        try:
            link_one.deliver()
        except BorrowSettledError as exc:
            print(f'   late duplicate command refused: {exc.code}')
        assert _snapshot(portal) == restored
        summary['steps'].append({'timing': 'drop_reply', 'recover': status, 'view': restored})
        spool.close()

        # Timing two: region borrow succeeds, the quarter borrow is retained.
        # First show that an open borrow blocks new collection while human edits
        # continue; then deliver the retained command and settle by ownership.
        print('2) region ok, quarter borrow held; busy + human edits; then delivery and settlement')
        spool = _open_spool(spool_path)
        link_two = _cut(portal, spool, order, 'export-two', 'borrow-2', 'quarter', 'hold')
        busy = collect_order(spool, portal, next_order, 'export-busy', borrow_id='borrow-busy')
        print(f'   new collect while open -> status={busy.status}, delivery={busy.delivery}')
        human = edit_filter(portal.view, 'quarter', 3, 'operator')
        print(f'   human re-selection still allowed: value={human.value} actor={human.actor}')
        edit_filter(portal.view, 'quarter', 1, 'operator')
        spool.close()

        spool = _open_spool(spool_path)
        status = recover_borrow(spool, portal, 'borrow-2')
        print(f'   recover_borrow -> {status} (human-owned quarter is left alone)')
        settled_view = _snapshot(portal)
        print('   view after settlement:', json.dumps(settled_view, ensure_ascii=False))
        try:
            link_two.deliver()
        except BorrowSettledError as exc:
            print(f'   re-delivered settled command refused: {exc.code}')
        assert _snapshot(portal) == settled_view
        assert recover_borrow(spool, portal, 'borrow-2') == 'settled'
        assert recover_borrow(spool, portal, 'never-seen') == 'unknown'
        summary['steps'].append({'timing': 'hold_human_kept', 'recover': status,
                                 'view': settled_view})
        spool.close()

        # Same timing without a human edit: the retained command still matches
        # its planned version, lands before settlement, and is returned because
        # the borrow still owns that item.
        print('3) retained quarter lands before settlement and is returned by ownership')
        spool = _open_spool(spool_path)
        link_three = _cut(portal, spool, order, 'export-four', 'borrow-4', 'quarter', 'hold')
        landed = link_three.deliver()
        print(f'   retained quarter command lands: value={landed.value} actor={landed.actor} '
              f'version={landed.version}')
        status = recover_borrow(spool, portal, 'borrow-4')
        print(f'   recover_borrow -> {status}')
        owned_view = _snapshot(portal)
        print('   view after settlement:', json.dumps(owned_view, ensure_ascii=False))
        try:
            link_three.deliver()
        except BorrowSettledError as exc:
            print(f'   late command after settlement refused: {exc.code}')
        assert _snapshot(portal) == owned_view
        summary['steps'].append({'timing': 'hold_lands_then_returned', 'recover': status,
                                 'view': owned_view})
        spool.close()

        # Restore reply lost: the effective restore happens exactly once and a
        # reopened follow-up run neither adds a write nor revives old commands.
        print('4) restore drop_reply, reopen, continue: restore happens once')
        spool = _open_spool(spool_path)
        link_four = _cut(portal, spool, order, 'export-five', 'borrow-5', 'region', 'drop_reply')
        restore_link = ScriptedLink()
        restore_link.arm('restore', 'region', 'drop_reply')
        try:
            recover_borrow(spool, portal, 'borrow-5', send_edit=restore_link.send_edit)
        except CaptureLinkLost:
            print('  * link lost on restore:region; restore already executed, reply dropped')
        once = read_filter(portal.view, 'region')
        print('   region after lost restore:', json.dumps(
            {'value': once.value, 'actor': once.actor, 'version': once.version},
            ensure_ascii=False))
        spool.close()

        spool = _open_spool(spool_path)
        status = recover_borrow(spool, portal, 'borrow-5')
        print(f'   recover_borrow after reopen -> {status}')
        assert read_filter(portal.view, 'region') == once
        for link in (link_four, restore_link):
            try:
                link.deliver()
            except BorrowSettledError as exc:
                print(f'   old command refused: {exc.code}')
        assert read_filter(portal.view, 'region') == once
        summary['steps'].append({'timing': 'restore_drop_reply', 'recover': status,
                                 'region_version': once.version})
        spool.close()

        # Next order: a fresh borrow after settlement completes a normal pickup,
        # and its preserved material is reclaimable by the original export id.
        print('5) next order pickup after settlement')
        spool = _open_spool(spool_path)
        result = collect_order(spool, portal, next_order, 'export-three', borrow_id='borrow-3')
        print(f'   collect_order -> status={result.status}')
        assert result.status == 'ready'
        evidence = result.delivery.evidence
        print(f'   rendered context: region={evidence.region} quarter={evidence.quarter} '
              f'digest={evidence.file_digest[:12]}...')
        reclaimed = collect_order(spool, portal, next_order, 'export-three')
        assert reclaimed.delivery == result.delivery
        assert load_delivery(spool, PORTAL, 'export-three') == result.delivery
        print('   same delivery returned on re-run and reload')
        final_view = _snapshot(portal)
        print('   final shared view:', json.dumps(final_view, ensure_ascii=False))
        summary['steps'].append({'next_order': 'ready', 'export_id': 'export-three',
                                 'view': final_view})
        spool.close()
        portal.close()

    print('SUMMARY ' + json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
