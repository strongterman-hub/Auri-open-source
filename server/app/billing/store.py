from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


CREDIT_MICROS = 1_000_000


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class PaymentOrder:
    order_id: str
    user_id: str
    amount_yuan: int
    credits: int
    status: str
    trade_no: str | None
    created_at: str
    updated_at: str


class BillingStore:
    """SQLite-backed Credits ledger and idempotent payment-order store."""

    def __init__(self, path: Path, *, welcome_credits: int = 500) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.welcome_credits = max(0, int(welcome_credits))
        self._init_lock = threading.Lock()
        self._initialized = False
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            with self._connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS credit_accounts (
                        user_id TEXT PRIMARY KEY,
                        balance_micros INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS credit_ledger (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT NOT NULL,
                        amount_micros INTEGER NOT NULL,
                        kind TEXT NOT NULL,
                        reference TEXT NOT NULL UNIQUE,
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_credit_ledger_user
                        ON credit_ledger(user_id, id DESC);

                    CREATE TABLE IF NOT EXISTS payment_orders (
                        order_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        amount_yuan INTEGER NOT NULL,
                        credits INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        trade_no TEXT UNIQUE,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        paid_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_payment_orders_user
                        ON payment_orders(user_id, created_at DESC);
                    """
                )
            self._initialized = True

    @staticmethod
    def _ensure_account(connection: sqlite3.Connection, user_id: str, now: str) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO credit_accounts
                (user_id, balance_micros, created_at, updated_at)
            VALUES (?, 0, ?, ?)
            """,
            (user_id, now, now),
        )

    def ensure_welcome_credit(self, user_id: str) -> bool:
        now = _utcnow()
        reference = f"welcome:{user_id}"
        amount_micros = self.welcome_credits * CREDIT_MICROS
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_account(connection, user_id, now)
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO credit_ledger
                    (user_id, amount_micros, kind, reference, metadata_json, created_at)
                VALUES (?, ?, 'welcome', ?, '{}', ?)
                """,
                (user_id, amount_micros, reference, now),
            )
            if cursor.rowcount:
                connection.execute(
                    """
                    UPDATE credit_accounts
                    SET balance_micros = balance_micros + ?, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (amount_micros, now, user_id),
                )
            connection.commit()
            return bool(cursor.rowcount)

    def balance_micros(self, user_id: str) -> int:
        self.ensure_welcome_credit(user_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT balance_micros FROM credit_accounts WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return int(row["balance_micros"]) if row else 0

    def apply_usage_debit(
        self,
        user_id: str,
        amount_micros: int,
        *,
        reference: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        amount_micros = max(0, int(amount_micros))
        if amount_micros == 0:
            return False
        self.ensure_welcome_credit(user_id)
        now = _utcnow()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO credit_ledger
                    (user_id, amount_micros, kind, reference, metadata_json, created_at)
                VALUES (?, ?, 'usage', ?, ?, ?)
                """,
                (
                    user_id,
                    -amount_micros,
                    reference,
                    json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")),
                    now,
                ),
            )
            if cursor.rowcount:
                connection.execute(
                    """
                    UPDATE credit_accounts
                    SET balance_micros = balance_micros - ?, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (amount_micros, now, user_id),
                )
            connection.commit()
            return bool(cursor.rowcount)

    def grant_credits(
        self,
        user_id: str,
        credits: int,
        *,
        reference: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        credits = int(credits)
        if credits <= 0:
            raise ValueError("credits must be positive")
        if not reference.strip():
            raise ValueError("reference is required")
        amount_micros = credits * CREDIT_MICROS
        now = _utcnow()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_account(connection, user_id, now)
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO credit_ledger
                    (user_id, amount_micros, kind, reference, metadata_json, created_at)
                VALUES (?, ?, 'manual_credit', ?, ?, ?)
                """,
                (
                    user_id,
                    amount_micros,
                    reference,
                    json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")),
                    now,
                ),
            )
            if cursor.rowcount:
                connection.execute(
                    """
                    UPDATE credit_accounts
                    SET balance_micros = balance_micros + ?, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (amount_micros, now, user_id),
                )
            connection.commit()
            return bool(cursor.rowcount)

    def create_order(self, user_id: str, amount_yuan: int, credits_per_yuan: int) -> PaymentOrder:
        now = _utcnow()
        order_id = (
            "AURI"
            + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            + secrets.token_hex(6).upper()
        )
        credits = int(amount_yuan) * int(credits_per_yuan)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO payment_orders
                    (order_id, user_id, amount_yuan, credits, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (order_id, user_id, amount_yuan, credits, now, now),
            )
        return PaymentOrder(order_id, user_id, amount_yuan, credits, "pending", None, now, now)

    def get_order(self, order_id: str, user_id: str | None = None) -> PaymentOrder | None:
        query = "SELECT * FROM payment_orders WHERE order_id = ?"
        params: tuple[Any, ...] = (order_id,)
        if user_id is not None:
            query += " AND user_id = ?"
            params = (order_id, user_id)
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        if row is None:
            return None
        return PaymentOrder(
            order_id=row["order_id"],
            user_id=row["user_id"],
            amount_yuan=int(row["amount_yuan"]),
            credits=int(row["credits"]),
            status=row["status"],
            trade_no=row["trade_no"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def mark_paid(
        self,
        order_id: str,
        *,
        trade_no: str,
        paid_amount_yuan: str,
    ) -> PaymentOrder | None:
        now = _utcnow()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM payment_orders WHERE order_id = ?", (order_id,)
            ).fetchone()
            try:
                amount_matches = (
                    Decimal(str(paid_amount_yuan)) == Decimal(int(row["amount_yuan"]))
                    if row is not None
                    else False
                )
            except InvalidOperation:
                amount_matches = False
            if row is None or not trade_no or not amount_matches:
                connection.rollback()
                return None
            if row["status"] != "paid":
                reference = f"recharge:{order_id}"
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO credit_ledger
                        (user_id, amount_micros, kind, reference, metadata_json, created_at)
                    VALUES (?, ?, 'recharge', ?, ?, ?)
                    """,
                    (
                        row["user_id"],
                        int(row["credits"]) * CREDIT_MICROS,
                        reference,
                        json.dumps(
                            {"order_id": order_id, "trade_no": trade_no},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        now,
                    ),
                )
                self._ensure_account(connection, row["user_id"], now)
                if cursor.rowcount:
                    connection.execute(
                        """
                        UPDATE credit_accounts
                        SET balance_micros = balance_micros + ?, updated_at = ?
                        WHERE user_id = ?
                        """,
                        (int(row["credits"]) * CREDIT_MICROS, now, row["user_id"]),
                    )
                connection.execute(
                    """
                    UPDATE payment_orders
                    SET status = 'paid', trade_no = ?, paid_at = ?, updated_at = ?
                    WHERE order_id = ?
                    """,
                    (trade_no, now, now, order_id),
                )
            connection.commit()
        return self.get_order(order_id)

    def mark_closed(self, order_id: str) -> None:
        now = _utcnow()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE payment_orders SET status = 'closed', updated_at = ?
                WHERE order_id = ? AND status = 'pending'
                """,
                (now, order_id),
            )

    def list_account_summaries(self) -> list[dict[str, Any]]:
        """Return read-only account and order totals without creating ledger rows."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    account.user_id,
                    account.balance_micros,
                    account.updated_at,
                    COUNT(orders.order_id) AS orders,
                    SUM(CASE WHEN orders.status = 'paid' THEN 1 ELSE 0 END) AS paid_orders,
                    SUM(CASE WHEN orders.status = 'paid' THEN orders.amount_yuan ELSE 0 END) AS paid_yuan
                FROM credit_accounts AS account
                LEFT JOIN payment_orders AS orders ON orders.user_id = account.user_id
                GROUP BY account.user_id, account.balance_micros, account.updated_at
                ORDER BY account.updated_at DESC, account.user_id
                """
            ).fetchall()
        return [
            {
                "user_id": row["user_id"],
                "balance_credits": round(int(row["balance_micros"]) / CREDIT_MICROS, 6),
                "updated_at": row["updated_at"],
                "orders": int(row["orders"] or 0),
                "paid_orders": int(row["paid_orders"] or 0),
                "paid_yuan": int(row["paid_yuan"] or 0),
            }
            for row in rows
        ]

    def delete_user(self, user_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM credit_ledger WHERE user_id = ?", (user_id,))
            connection.execute("DELETE FROM payment_orders WHERE user_id = ?", (user_id,))
            connection.execute("DELETE FROM credit_accounts WHERE user_id = ?", (user_id,))
