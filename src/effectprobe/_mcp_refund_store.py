"""Private SQLite state and append-only evidence for the MCP refund fixture."""

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from effectprobe._refund_comparison import RefundReceipt, RefundState
from effectprobe._semantic_kernel import SurfaceObservation

_PAYMENT_ID = "payment/refund-001"
_PAYMENT_MINOR_UNITS = 10_000


@dataclass(frozen=True, slots=True)
class McpRefundEvent:
    """One committed refund, separate from transport-delivery evidence."""

    refund_id: str
    payment_id: str
    amount_minor_units: int
    operation_key: str | None


@dataclass(frozen=True, slots=True)
class McpDelivery:
    """One transport delivery observed by the fixture, not effect history."""

    ordinal: int
    mcp_request_id: str
    operation_key: str
    process_id: int


type McpRefundObservation = SurfaceObservation[RefundState, McpRefundEvent]


def initialize_refund_database(path: Path) -> None:
    """Create one fresh isolated refund world."""

    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA journal_mode = DELETE;
            CREATE TABLE payments (
                payment_id TEXT PRIMARY KEY,
                payment_minor_units INTEGER NOT NULL,
                refunded_minor_units INTEGER NOT NULL
            );
            CREATE TABLE refund_events (
                ordinal INTEGER PRIMARY KEY AUTOINCREMENT,
                refund_id TEXT NOT NULL UNIQUE,
                payment_id TEXT NOT NULL,
                amount_minor_units INTEGER NOT NULL,
                operation_key TEXT
            );
            CREATE TRIGGER refund_events_no_update
            BEFORE UPDATE ON refund_events
            BEGIN
                SELECT RAISE(ABORT, 'refund history is append-only');
            END;
            CREATE TRIGGER refund_events_no_delete
            BEFORE DELETE ON refund_events
            BEGIN
                SELECT RAISE(ABORT, 'refund history is append-only');
            END;
            CREATE TABLE keyed_receipts (
                operation_key TEXT PRIMARY KEY,
                refund_id TEXT NOT NULL,
                payment_id TEXT NOT NULL,
                amount_minor_units INTEGER NOT NULL
            );
            CREATE TABLE mcp_deliveries (
                ordinal INTEGER PRIMARY KEY AUTOINCREMENT,
                mcp_request_id TEXT NOT NULL,
                operation_key TEXT NOT NULL,
                process_id INTEGER NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO payments VALUES (?, ?, 0)",
            (_PAYMENT_ID, _PAYMENT_MINOR_UNITS),
        )


def commit_refund(
    path: Path,
    *,
    mode: Literal["unsafe", "keyed"],
    payment_id: str,
    amount_minor_units: int,
    operation_key: str,
    mcp_request_id: str,
) -> RefundReceipt:
    """Commit or deduplicate one refund before returning its receipt."""

    with sqlite3.connect(path, isolation_level=None) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO mcp_deliveries(mcp_request_id, operation_key, process_id)
            VALUES (?, ?, ?)
            """,
            (mcp_request_id, operation_key, os.getpid()),
        )
        if mode == "keyed":
            stored = connection.execute(
                """
                SELECT refund_id, payment_id, amount_minor_units
                FROM keyed_receipts WHERE operation_key = ?
                """,
                (operation_key,),
            ).fetchone()
            if stored is not None:
                connection.commit()
                return RefundReceipt(str(stored[0]), str(stored[1]), int(stored[2]))

        payment = connection.execute(
            """
            SELECT payment_minor_units, refunded_minor_units
            FROM payments WHERE payment_id = ?
            """,
            (payment_id,),
        ).fetchone()
        if payment is None:
            raise ValueError(f"unknown payment: {payment_id}")
        if amount_minor_units <= 0 or int(payment[1]) + amount_minor_units > int(payment[0]):
            raise ValueError("refund amount exceeds the available payment balance")

        ordinal = int(connection.execute("SELECT COUNT(*) FROM refund_events").fetchone()[0]) + 1
        refund_id = f"refund/{ordinal}"
        provider_key = operation_key if mode == "keyed" else None
        connection.execute(
            """
            INSERT INTO refund_events(
                refund_id, payment_id, amount_minor_units, operation_key
            ) VALUES (?, ?, ?, ?)
            """,
            (refund_id, payment_id, amount_minor_units, provider_key),
        )
        connection.execute(
            """
            UPDATE payments SET refunded_minor_units = refunded_minor_units + ?
            WHERE payment_id = ?
            """,
            (amount_minor_units, payment_id),
        )
        receipt = RefundReceipt(refund_id, payment_id, amount_minor_units)
        if mode == "keyed":
            connection.execute(
                "INSERT INTO keyed_receipts VALUES (?, ?, ?, ?)",
                (operation_key, refund_id, payment_id, amount_minor_units),
            )
        connection.commit()
        return receipt


def observe_refunds(path: Path) -> McpRefundObservation:
    """Read current state and complete append-only committed history."""

    with sqlite3.connect(path) as connection:
        payment = connection.execute(
            """
            SELECT payment_id, payment_minor_units, refunded_minor_units
            FROM payments WHERE payment_id = ?
            """,
            (_PAYMENT_ID,),
        ).fetchone()
        if payment is None:
            raise ValueError("refund fixture payment is missing")
        key_count = int(connection.execute("SELECT COUNT(*) FROM keyed_receipts").fetchone()[0])
        rows = connection.execute(
            """
            SELECT refund_id, payment_id, amount_minor_units, operation_key
            FROM refund_events ORDER BY ordinal
            """
        ).fetchall()
    return SurfaceObservation(
        state=RefundState(str(payment[0]), int(payment[1]), int(payment[2]), key_count),
        history=tuple(
            McpRefundEvent(str(row[0]), str(row[1]), int(row[2]), row[3]) for row in rows
        ),
    )


def read_mcp_deliveries(path: Path) -> tuple[McpDelivery, ...]:
    """Read transport evidence separately from committed effects."""

    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            """
            SELECT ordinal, mcp_request_id, operation_key, process_id
            FROM mcp_deliveries ORDER BY ordinal
            """
        ).fetchall()
    return tuple(McpDelivery(int(row[0]), str(row[1]), str(row[2]), int(row[3])) for row in rows)
