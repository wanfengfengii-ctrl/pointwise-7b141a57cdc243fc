from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import asdict, dataclass

from capture.evidence import RenderEvidence
from capture.orders import CaptureOrder
from portal.download import DownloadArtifact


@dataclass(frozen=True)
class Delivery:
    order: CaptureOrder
    content: bytes
    evidence: RenderEvidence


def ensure_delivery_schema(connection: sqlite3.Connection) -> None:
    with connection:
        connection.execute('''
            CREATE TABLE IF NOT EXISTS capture_deliveries (
                portal_id TEXT NOT NULL,
                export_id TEXT NOT NULL,
                order_json TEXT NOT NULL,
                content BLOB NOT NULL,
                evidence_json TEXT NOT NULL,
                PRIMARY KEY (portal_id, export_id)
            )
        ''')
        connection.execute('''
            CREATE TABLE IF NOT EXISTS capture_borrows (
                borrow_id TEXT PRIMARY KEY NOT NULL,
                portal_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                order_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('open', 'settled')),
                created_at TEXT NOT NULL,
                settled_at TEXT
            )
        ''')
        connection.execute('''
            CREATE TABLE IF NOT EXISTS capture_borrow_items (
                borrow_id TEXT NOT NULL,
                field TEXT NOT NULL,
                target_value TEXT NOT NULL,
                prev_value TEXT NOT NULL,
                prev_version INTEGER NOT NULL,
                PRIMARY KEY (borrow_id, field)
            )
        ''')


def _encode(value) -> str:
    return json.dumps(value, sort_keys=True)


def _decode(text: str):
    return json.loads(text)


def _stamp() -> str:
    return time.time_ns().to_bytes(16, 'big').hex()


def record_borrow(
    connection: sqlite3.Connection,
    borrow_id: str,
    portal_id: str,
    session_id: str,
    order: CaptureOrder,
    items: list[dict],
) -> None:
    """Persist the borrow attempt and every intended filter write before sending.

    Each item carries the filter field, the desired target value, and the exact
    previous position (value and version) read before the write was planned.
    """
    with connection:
        connection.execute('''
            INSERT INTO capture_borrows
                (borrow_id, portal_id, session_id, order_json, status, created_at, settled_at)
            VALUES (?, ?, ?, ?, 'open', ?, NULL)
        ''', (
            borrow_id, portal_id, session_id, json.dumps(asdict(order), sort_keys=True), _stamp(),
        ))
        connection.executemany('''
            INSERT INTO capture_borrow_items
                (borrow_id, field, target_value, prev_value, prev_version)
            VALUES (?, ?, ?, ?, ?)
        ''', [
            (
                borrow_id,
                item['field'],
                _encode(item['target_value']),
                _encode(item['previous_value']),
                item['previous_version'],
            )
            for item in items
        ])


def load_borrow(connection: sqlite3.Connection, borrow_id: str) -> dict | None:
    row = connection.execute('''
        SELECT borrow_id, portal_id, session_id, order_json, status, created_at, settled_at
        FROM capture_borrows WHERE borrow_id = ?
    ''', (borrow_id,)).fetchone()
    if row is None:
        return None
    return {
        'borrow_id': row[0],
        'portal_id': row[1],
        'session_id': row[2],
        'order': CaptureOrder(**json.loads(row[3])),
        'status': row[4],
        'created_at': row[5],
        'settled_at': row[6],
    }


def load_borrow_items(connection: sqlite3.Connection, borrow_id: str) -> list[dict]:
    rows = connection.execute('''
        SELECT field, target_value, prev_value, prev_version
        FROM capture_borrow_items WHERE borrow_id = ? ORDER BY rowid
    ''', (borrow_id,)).fetchall()
    return [
        {
            'field': row[0],
            'target_value': _decode(row[1]),
            'previous_value': _decode(row[2]),
            'previous_version': row[3],
        }
        for row in rows
    ]


def has_open_borrow(connection: sqlite3.Connection, portal_id: str) -> bool:
    row = connection.execute(
        'SELECT 1 FROM capture_borrows WHERE portal_id = ? AND status = ? LIMIT 1',
        (portal_id, 'open'),
    ).fetchone()
    return row is not None


def mark_borrow_settled(connection: sqlite3.Connection, borrow_id: str) -> None:
    with connection:
        connection.execute(
            "UPDATE capture_borrows SET status = 'settled', settled_at = ? WHERE borrow_id = ?",
            (_stamp(), borrow_id),
        )


def load_delivery(connection: sqlite3.Connection, portal_id: str, export_id: str) -> Delivery | None:
    row = connection.execute('''
        SELECT order_json, content, evidence_json FROM capture_deliveries
        WHERE portal_id = ? AND export_id = ?
    ''', (portal_id, export_id)).fetchone()
    if row is None:
        return None
    order_json, content, evidence_json = row
    return Delivery(CaptureOrder(**json.loads(order_json)), content, RenderEvidence(**json.loads(evidence_json)))


def stage_delivery(
    connection: sqlite3.Connection, order: CaptureOrder, artifact: DownloadArtifact, evidence: RenderEvidence
) -> Delivery:
    binding = (order.order_id, artifact.portal_id, artifact.export_id, artifact.render_sequence)
    if order.portal_id != artifact.portal_id or binding != (
        evidence.order_id, evidence.portal_id, evidence.export_id, evidence.render_sequence,
    ):
        raise ValueError('capture delivery identity mismatch')
    if hashlib.sha256(artifact.content).hexdigest() != evidence.file_digest:
        raise ValueError('capture delivery digest mismatch')
    delivery = Delivery(order, artifact.content, evidence)
    with connection:
        connection.execute('''
            INSERT INTO capture_deliveries (portal_id, export_id, order_json, content, evidence_json)
            VALUES (?, ?, ?, ?, ?) ON CONFLICT (portal_id, export_id) DO NOTHING
        ''', (
            artifact.portal_id, artifact.export_id, json.dumps(asdict(order), sort_keys=True),
            artifact.content, json.dumps(asdict(evidence), sort_keys=True),
        ))
        if load_delivery(connection, artifact.portal_id, artifact.export_id) != delivery:
            raise ValueError('export already bound to another capture delivery')
    return delivery
