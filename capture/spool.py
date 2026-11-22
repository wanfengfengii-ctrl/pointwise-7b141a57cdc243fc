from __future__ import annotations

import hashlib
import json
import sqlite3
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
