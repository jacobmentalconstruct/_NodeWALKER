"""
Session Database for storing queries, activations, and summaries.

All session/activation data is stored in nodewalker_sessions.db,
separate from the read-only Cartridge DB.
"""

import sqlite3
import os
from pathlib import Path
from typing import List, Tuple, Optional
from uuid import uuid4
from datetime import datetime

from .activation_types import ActivationEvent, ActivationKind, TargetType


class SessionDB:
    """Handle all session-related CRUD operations."""

    def __init__(self, db_path: str = None):
        """Initialize SessionDB with optional custom path."""
        if db_path is None:
            # Default: store in user's home directory
            db_dir = Path.home() / ".nodewalker"
            db_dir.mkdir(exist_ok=True)
            db_path = str(db_dir / "nodewalker_sessions.db")

        self.db_path = db_path
        self.ensure_schema()

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_schema(self) -> None:
        """Create tables if they don't exist (idempotent)."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Create sessions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                ended_at TEXT
            )
        """)

        # Create queries table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS queries (
                query_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                query_text TEXT NOT NULL,
                model TEXT,
                ts_start TEXT NOT NULL,
                ts_end TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions (session_id)
            )
        """)

        # Create activations table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS activations (
                event_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                query_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                kind TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                weight REAL NOT NULL,
                meta_json TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions (session_id),
                FOREIGN KEY (query_id) REFERENCES queries (query_id)
            )
        """)

        # Create summaries table (for 3-tier memory)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS summaries (
                summary_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                tier INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions (session_id)
            )
        """)

        conn.commit()
        conn.close()

    def create_session(self) -> str:
        """Create a new session and return session_id."""
        session_id = str(uuid4())
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO sessions (session_id, created_at)
            VALUES (?, ?)
        """, (session_id, datetime.utcnow().isoformat() + "Z"))

        conn.commit()
        conn.close()
        return session_id

    def start_query(self, session_id: str, query_text: str, model: str = None) -> str:
        """Start a new query and return query_id."""
        query_id = str(uuid4())
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO queries (query_id, session_id, query_text, model, ts_start)
            VALUES (?, ?, ?, ?, ?)
        """, (query_id, session_id, query_text, model, datetime.utcnow().isoformat() + "Z"))

        conn.commit()
        conn.close()
        return query_id

    def end_query(self, query_id: str) -> None:
        """Mark a query as finished."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE queries
            SET ts_end = ?
            WHERE query_id = ?
        """, (datetime.utcnow().isoformat() + "Z", query_id))

        conn.commit()
        conn.close()

    def insert_activation(self, event: ActivationEvent) -> None:
        """Insert an activation event into the database."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO activations (
                event_id, session_id, query_id, ts, kind, target_type,
                target_id, weight, meta_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event.event_id,
            event.session_id,
            event.query_id,
            event.ts,
            event.kind.value,
            event.target_type.value,
            event.target_id,
            event.weight,
            str(event.meta) if event.meta else None
        ))

        conn.commit()
        conn.close()

    def get_top_activations(
        self, query_id: str, limit: int = 25
    ) -> List[Tuple[str, str, float]]:
        """
        Get top activated targets for a query (by cumulative weight).

        Returns list of (target_type, target_id, cumulative_weight) tuples.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT target_type, target_id, SUM(weight) as total_weight
            FROM activations
            WHERE query_id = ?
            GROUP BY target_type, target_id
            ORDER BY total_weight DESC
            LIMIT ?
        """, (query_id, limit))

        results = cursor.fetchall()
        conn.close()

        return [(row[0], row[1], row[2]) for row in results]

    def insert_summary(self, session_id: str, tier: int, content: str) -> str:
        """Insert a memory summary at a given tier."""
        summary_id = str(uuid4())
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO summaries (summary_id, session_id, tier, content, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (summary_id, session_id, tier, content, datetime.utcnow().isoformat() + "Z"))

        conn.commit()
        conn.close()
        return summary_id

    def get_summaries(self, session_id: str, tier: int) -> List[str]:
        """Get all summaries for a session at a given tier."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT content
            FROM summaries
            WHERE session_id = ? AND tier = ?
            ORDER BY created_at DESC
        """, (session_id, tier))

        results = cursor.fetchall()
        conn.close()

        return [row[0] for row in results]
