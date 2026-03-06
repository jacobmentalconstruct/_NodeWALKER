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

        # Mission logging: per-query walk runs
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS query_runs (
                run_id TEXT PRIMARY KEY,
                query_id TEXT NOT NULL,
                walk_id TEXT NOT NULL,
                facet_id TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                total_nodes INTEGER DEFAULT 0,
                total_evidence INTEGER DEFAULT 0,
                sufficiency_level TEXT,
                reason TEXT,
                FOREIGN KEY (query_id) REFERENCES queries (query_id)
            )
        """)

        # Mission logging: per-step records
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS query_steps (
                step_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                walk_id TEXT NOT NULL,
                facet_id TEXT,
                node_id TEXT,
                chunk_id TEXT,
                score REAL,
                timestamp TEXT NOT NULL,
                reason TEXT,
                FOREIGN KEY (run_id) REFERENCES query_runs (run_id)
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

    # =========================================================================
    # Mission Logging
    # =========================================================================

    def insert_query_run(
        self, query_id: str, walk_id: str, facet_id: str = None,
    ) -> str:
        """Create a new query run record. Returns run_id."""
        run_id = str(uuid4())
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO query_runs (run_id, query_id, walk_id, facet_id, started_at)
            VALUES (?, ?, ?, ?, ?)
        """, (run_id, query_id, walk_id, facet_id,
              datetime.utcnow().isoformat() + "Z"))

        conn.commit()
        conn.close()
        return run_id

    def end_query_run(
        self, run_id: str, total_nodes: int = 0, total_evidence: int = 0,
        sufficiency_level: str = "", reason: str = "",
    ) -> None:
        """Mark a query run as finished with stats."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE query_runs
            SET finished_at = ?, total_nodes = ?, total_evidence = ?,
                sufficiency_level = ?, reason = ?
            WHERE run_id = ?
        """, (datetime.utcnow().isoformat() + "Z", total_nodes,
              total_evidence, sufficiency_level, reason, run_id))

        conn.commit()
        conn.close()

    def insert_query_step(
        self, run_id: str, walk_id: str, facet_id: str = None,
        node_id: str = None, chunk_id: str = None,
        score: float = 0.0, reason: str = "",
    ) -> str:
        """Log a single expansion step. Returns step_id."""
        step_id = str(uuid4())
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO query_steps
                (step_id, run_id, walk_id, facet_id, node_id, chunk_id, score, timestamp, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (step_id, run_id, walk_id, facet_id, node_id, chunk_id,
              score, datetime.utcnow().isoformat() + "Z", reason))

        conn.commit()
        conn.close()
        return step_id

    def get_query_steps(self, run_id: str) -> List[dict]:
        """Get all steps for a query run."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT step_id, facet_id, node_id, chunk_id, score, timestamp, reason
            FROM query_steps
            WHERE run_id = ?
            ORDER BY timestamp
        """, (run_id,))

        results = cursor.fetchall()
        conn.close()

        return [
            {
                "step_id": row[0], "facet_id": row[1],
                "node_id": row[2], "chunk_id": row[3],
                "score": row[4], "timestamp": row[5], "reason": row[6],
            }
            for row in results
        ]

    def summarize_run(self, run_id: str) -> dict:
        """Get summary stats for a run."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT total_nodes, total_evidence, sufficiency_level, reason,
                   started_at, finished_at
            FROM query_runs
            WHERE run_id = ?
        """, (run_id,))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return {}

        return {
            "total_nodes": row[0], "total_evidence": row[1],
            "sufficiency_level": row[2], "reason": row[3],
            "started_at": row[4], "finished_at": row[5],
        }
