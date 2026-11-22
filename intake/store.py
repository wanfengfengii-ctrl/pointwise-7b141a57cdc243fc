from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass

from archive.store import publication_transaction
from capture.orders import CaptureOrder
from intake.context import ReceivedContext as ReceivedContext, match_context, read_received_context
from intake.files import ReceivedFile, read_received_file


@dataclass(frozen=True)
class Candidate:
    file: ReceivedFile | None = None
    context: ReceivedContext | None = None
    rejection: str | None = None


def ensure_intake_schema(connection: sqlite3.Connection) -> None:
    with publication_transaction(connection):
        connection.execute('''
            CREATE TABLE IF NOT EXISTS intake_orders (
                order_id TEXT PRIMARY KEY NOT NULL,
                portal_id TEXT NOT NULL,
                region TEXT NOT NULL,
                quarter INTEGER NOT NULL CHECK (quarter BETWEEN 1 AND 4),
                shift TEXT NOT NULL,
                position INTEGER NOT NULL CHECK (position > 0),
                UNIQUE (shift, position),
                UNIQUE (order_id, portal_id)
            )
        ''')
        connection.execute('''
            CREATE TABLE IF NOT EXISTS intake_material (
                order_id TEXT NOT NULL,
                portal_id TEXT NOT NULL,
                export_id TEXT NOT NULL,
                file_bytes BLOB,
                evidence_json TEXT,
                rejection TEXT,
                PRIMARY KEY (order_id, portal_id, export_id),
                FOREIGN KEY (order_id, portal_id) REFERENCES intake_orders (order_id, portal_id),
                CHECK (file_bytes IS NOT NULL OR evidence_json IS NOT NULL)
            )
        ''')


def read_registered_order(connection: sqlite3.Connection, order_id: str) -> CaptureOrder | None:
    row = connection.execute('''
        SELECT order_id, portal_id, region, quarter, shift, position
        FROM intake_orders WHERE order_id = ?
    ''', (order_id,)).fetchone()
    return None if row is None else CaptureOrder(*row)


def _register_order(connection: sqlite3.Connection, order: CaptureOrder) -> str | None:
    saved = read_registered_order(connection, order.order_id)
    if saved is not None:
        return None if saved == order else 'ORDER_FACTS_CONFLICT'
    occupied = connection.execute('SELECT 1 FROM intake_orders WHERE shift = ? AND position = ?', (
        order.shift, order.position,
    )).fetchone()
    if occupied is not None:
        return 'SHIFT_POSITION_CONFLICT'
    connection.execute('''
        INSERT INTO intake_orders (order_id, portal_id, region, quarter, shift, position)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (order.order_id, order.portal_id, order.region, order.quarter, order.shift, order.position))
    return None


def load_candidate(connection: sqlite3.Connection, order: CaptureOrder, export_id: str) -> Candidate:
    key = order.order_id, order.portal_id, export_id
    saved = connection.execute('''
        SELECT file_bytes, evidence_json, rejection FROM intake_material
        WHERE order_id = ? AND portal_id = ? AND export_id = ?
    ''', key).fetchone()
    if saved is None:
        return Candidate()
    original, evidence, rejection = saved
    file = None if original is None else read_received_file(*key, original)
    context = None if evidence is None else read_received_context(json.loads(evidence))
    return Candidate(file, context, rejection)


def store_material(
    connection: sqlite3.Connection, order: CaptureOrder, export_id: str, material: ReceivedFile | ReceivedContext
) -> str | None:
    key = order.order_id, order.portal_id, export_id
    if key != (material.order_id, material.portal_id, material.export_id):
        return 'MATERIAL_IDENTITY_MISMATCH'
    with publication_transaction(connection):
        conflict = _register_order(connection, order)
        if conflict:
            return conflict
        saved = load_candidate(connection, order, export_id)
        file, context = saved.file, saved.context
        if isinstance(material, ReceivedFile):
            if file is not None and file != material:
                return 'FILE_CONFLICT'
            file = material
        else:
            if context is not None and context != material:
                return 'CONTEXT_CONFLICT'
            context = material
        if file is not None and context is not None:
            conflict = match_context(file, context)
            if conflict:
                return conflict
        connection.execute('''
            INSERT INTO intake_material (order_id, portal_id, export_id, file_bytes, evidence_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (order_id, portal_id, export_id) DO UPDATE SET
                file_bytes = excluded.file_bytes, evidence_json = excluded.evidence_json
        ''', (*key, None if file is None else file.content,
              None if context is None else json.dumps(asdict(context), sort_keys=True)))
    return None


def record_rejection(connection: sqlite3.Connection, order: CaptureOrder, export_id: str, code: str) -> None:
    with publication_transaction(connection):
        connection.execute('''
            UPDATE intake_material SET rejection = COALESCE(rejection, ?)
            WHERE order_id = ? AND portal_id = ? AND export_id = ?
        ''', (code, order.order_id, order.portal_id, export_id))
