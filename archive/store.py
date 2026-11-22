from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def publication_transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    connection.execute('BEGIN IMMEDIATE')
    try:
        yield connection
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise


def ensure_archive_schema(connection: sqlite3.Connection) -> None:
    with publication_transaction(connection):
        connection.execute('''
            CREATE TABLE IF NOT EXISTS archive_results (
                result_id TEXT PRIMARY KEY NOT NULL,
                order_id TEXT NOT NULL UNIQUE,
                portal_id TEXT NOT NULL,
                export_id TEXT NOT NULL,
                region TEXT NOT NULL,
                quarter INTEGER NOT NULL CHECK (quarter BETWEEN 1 AND 4),
                shift TEXT NOT NULL,
                position INTEGER NOT NULL CHECK (position > 0),
                evidence_json TEXT NOT NULL,
                file_digest TEXT NOT NULL,
                missing_count INTEGER NOT NULL CHECK (missing_count >= 0),
                UNIQUE (shift, position),
                UNIQUE (portal_id, export_id)
            )
        ''')
        connection.execute('''
            CREATE TABLE IF NOT EXISTS result_rows (
                result_id TEXT NOT NULL REFERENCES archive_results (result_id),
                record_number INTEGER NOT NULL CHECK (record_number > 0),
                inspection_id TEXT NOT NULL,
                raw_pressure TEXT NOT NULL,
                pressure_pa TEXT,
                pressure_kpa TEXT,
                PRIMARY KEY (result_id, record_number),
                UNIQUE (result_id, inspection_id),
                CHECK ((pressure_pa IS NULL) = (pressure_kpa IS NULL))
            )
        ''')
        connection.execute('''
            CREATE TABLE IF NOT EXISTS receipts (
                receipt_id TEXT PRIMARY KEY NOT NULL,
                result_id TEXT NOT NULL UNIQUE REFERENCES archive_results (result_id)
            )
        ''')


def open_archive(path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA foreign_keys = ON')
        ensure_archive_schema(connection)
    except BaseException:
        connection.close()
        raise
    return connection
