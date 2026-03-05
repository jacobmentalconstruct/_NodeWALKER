"""
Notes Module
Decision memory and anti-data storage.

Tables:
- notes_events: Append-only journal of events
- anti_data_rules: Deny/penalty rules
- cartridge_profiles: Learned cartridge fingerprints

This is a SEPARATE database from the cartridge (global memory).
"""

import sqlite3
import json
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
from contextlib import contextmanager

from src.walker.types import NotesEvent, AntiDataRule, AntiDataAction, CartridgeProfile


class NotesDB:
    """
    Global notes database for decision memory.
    Separate from cartridge databases.
    """
    
    SCHEMA_VERSION = 1
    
    def __init__(self, path: Optional[Path] = None):
        if path is None:
            path = Path.home() / ".nodewalker" / "notes.db"
        
        self.path = Path(path)
        self._conn: Optional[sqlite3.Connection] = None
    
    @property
    def is_connected(self) -> bool:
        return self._conn is not None
    
    @contextmanager
    def cursor(self):
        if not self._conn:
            raise RuntimeError("Notes DB not connected")
        cur = self._conn.cursor()
        try:
            yield cur
            self._conn.commit()
        finally:
            cur.close()
    
    # =========================================================================
    # Connection Management
    # =========================================================================
    
    def connect(self):
        """Connect to notes database, creating if needed"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        
        self._ensure_schema()
    
    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
    
    def _ensure_schema(self):
        """Create tables if they don't exist"""
        with self.cursor() as cur:
            cur.executescript("""
                -- Schema version tracking
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                
                -- Append-only event journal
                CREATE TABLE IF NOT EXISTS notes_events (
                    event_id TEXT PRIMARY KEY,
                    ts TEXT NOT NULL,
                    scope_type TEXT NOT NULL,
                    scope_id TEXT,
                    event_type TEXT NOT NULL,
                    severity INTEGER DEFAULT 0,
                    summary TEXT,
                    details_json TEXT DEFAULT '{}'
                );
                
                -- Anti-data rules
                CREATE TABLE IF NOT EXISTS anti_data_rules (
                    rule_id TEXT PRIMARY KEY,
                    match_type TEXT NOT NULL,
                    match_value TEXT NOT NULL,
                    action TEXT NOT NULL,
                    penalty REAL DEFAULT 0.0,
                    reason TEXT,
                    created_at TEXT,
                    expires_at TEXT
                );
                
                -- Learned cartridge profiles
                CREATE TABLE IF NOT EXISTS cartridge_profiles (
                    cartridge_id TEXT PRIMARY KEY,
                    signature_hash TEXT,
                    success_modes_json TEXT DEFAULT '[]',
                    failure_modes_json TEXT DEFAULT '[]',
                    walk_count INTEGER DEFAULT 0,
                    last_seen TEXT,
                    recommended_policy_json TEXT
                );
                
                -- Indices
                CREATE INDEX IF NOT EXISTS idx_events_scope 
                    ON notes_events(scope_type, scope_id);
                CREATE INDEX IF NOT EXISTS idx_events_ts 
                    ON notes_events(ts);
                CREATE INDEX IF NOT EXISTS idx_rules_match 
                    ON anti_data_rules(match_type, match_value);
                CREATE INDEX IF NOT EXISTS idx_profiles_sig 
                    ON cartridge_profiles(signature_hash);
            """)
            
            # Set schema version
            cur.execute("""
                INSERT OR REPLACE INTO schema_meta (key, value)
                VALUES ('version', ?)
            """, (str(self.SCHEMA_VERSION),))
    
    # =========================================================================
    # Event Logging
    # =========================================================================
    
    def log_event(self, 
                   scope_type: str,
                   event_type: str,
                   summary: str,
                   scope_id: str = "",
                   severity: int = 0,
                   details: Optional[Dict] = None) -> str:
        """
        Log an event to the journal.
        
        Args:
            scope_type: cartridge | file | chunk | pipeline | tool | global
            event_type: ingest | walk | error | heuristic | warning | ban | allow
            summary: Short description
            scope_id: Relevant ID (cartridge_id, file_cid, etc.)
            severity: 0-5
            details: Additional structured data
        
        Returns:
            event_id
        """
        event_id = str(uuid.uuid4())
        ts = datetime.utcnow().isoformat()
        details_json = json.dumps(details or {})
        
        with self.cursor() as cur:
            cur.execute("""
                INSERT INTO notes_events 
                (event_id, ts, scope_type, scope_id, event_type, severity, summary, details_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (event_id, ts, scope_type, scope_id, event_type, severity, summary, details_json))
        
        return event_id
    
    def log_walk_start(self, cartridge_id: str, query: str) -> str:
        """Log walk start event"""
        return self.log_event(
            scope_type="cartridge",
            scope_id=cartridge_id,
            event_type="walk",
            summary=f"Walk started: {query[:50]}",
            details={"query": query}
        )
    
    def log_walk_end(self, cartridge_id: str, 
                      chunks_collected: int,
                      elapsed_ms: int,
                      success: bool = True) -> str:
        """Log walk end event"""
        return self.log_event(
            scope_type="cartridge",
            scope_id=cartridge_id,
            event_type="walk",
            summary=f"Walk {'completed' if success else 'failed'}: {chunks_collected} chunks in {elapsed_ms}ms",
            severity=0 if success else 2,
            details={
                "chunks_collected": chunks_collected,
                "elapsed_ms": elapsed_ms,
                "success": success
            }
        )
    
    def log_error(self, scope_type: str, scope_id: str,
                   error: str, severity: int = 3) -> str:
        """Log an error event"""
        return self.log_event(
            scope_type=scope_type,
            scope_id=scope_id,
            event_type="error",
            summary=error[:200],
            severity=severity,
            details={"error": error}
        )
    
    def log_heuristic(self, cartridge_id: str,
                       heuristic: str,
                       value: Any) -> str:
        """Log a learned heuristic"""
        return self.log_event(
            scope_type="cartridge",
            scope_id=cartridge_id,
            event_type="heuristic",
            summary=f"Learned: {heuristic}",
            details={"heuristic": heuristic, "value": value}
        )
    
    def get_events(self, scope_type: Optional[str] = None,
                    scope_id: Optional[str] = None,
                    event_type: Optional[str] = None,
                    limit: int = 100) -> List[NotesEvent]:
        """Query events with filters"""
        query = "SELECT * FROM notes_events WHERE 1=1"
        params = []
        
        if scope_type:
            query += " AND scope_type = ?"
            params.append(scope_type)
        if scope_id:
            query += " AND scope_id = ?"
            params.append(scope_id)
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        
        query += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        
        with self.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
            
            return [
                NotesEvent(
                    event_id=row["event_id"],
                    ts=row["ts"],
                    scope_type=row["scope_type"],
                    scope_id=row["scope_id"] or "",
                    event_type=row["event_type"],
                    severity=row["severity"],
                    summary=row["summary"] or "",
                    details_json=row["details_json"] or "{}",
                )
                for row in rows
            ]
    
    # =========================================================================
    # Anti-Data Rules
    # =========================================================================
    
    def add_rule(self,
                  match_type: str,
                  match_value: str,
                  action: AntiDataAction,
                  penalty: float = 0.0,
                  reason: str = "",
                  expires_at: Optional[str] = None) -> str:
        """
        Add an anti-data rule.
        
        Args:
            match_type: exact | prefix | regex | jsonpath | cid_set
            match_value: Value to match against
            action: block | penalize | warn
            penalty: Score penalty (for penalize action)
            reason: Why this rule exists
            expires_at: Optional expiration timestamp
        
        Returns:
            rule_id
        """
        rule_id = str(uuid.uuid4())
        created_at = datetime.utcnow().isoformat()
        
        with self.cursor() as cur:
            cur.execute("""
                INSERT INTO anti_data_rules
                (rule_id, match_type, match_value, action, penalty, reason, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (rule_id, match_type, match_value, action.value, penalty, reason, created_at, expires_at))
        
        return rule_id
    
    def remove_rule(self, rule_id: str):
        """Remove an anti-data rule"""
        with self.cursor() as cur:
            cur.execute("DELETE FROM anti_data_rules WHERE rule_id = ?", (rule_id,))
    
    def get_rules(self, match_type: Optional[str] = None) -> List[AntiDataRule]:
        """Get all active rules"""
        query = """
            SELECT * FROM anti_data_rules 
            WHERE (expires_at IS NULL OR expires_at > datetime('now'))
        """
        params = []
        
        if match_type:
            query += " AND match_type = ?"
            params.append(match_type)
        
        with self.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
            
            return [
                AntiDataRule(
                    rule_id=row["rule_id"],
                    match_type=row["match_type"],
                    match_value=row["match_value"],
                    action=AntiDataAction(row["action"]),
                    penalty=row["penalty"] or 0.0,
                    reason=row["reason"] or "",
                    created_at=row["created_at"] or "",
                    expires_at=row["expires_at"],
                )
                for row in rows
            ]
    
    def get_rules_for_value(self, value: str) -> List[AntiDataRule]:
        """Get rules that might match a value"""
        # For efficiency, we fetch all rules and filter in Python
        # In production, this could be optimized with better indexing
        all_rules = self.get_rules()
        return [r for r in all_rules if self._rule_might_match(r, value)]
    
    def _rule_might_match(self, rule: AntiDataRule, value: str) -> bool:
        """Quick check if rule might match value"""
        if rule.match_type == "exact":
            return rule.match_value == value
        elif rule.match_type == "prefix":
            return value.startswith(rule.match_value)
        elif rule.match_type == "cid_set":
            cids = set(rule.match_value.split(","))
            return value in cids
        # For regex/jsonpath, always return True (need full check)
        return True
    
    # =========================================================================
    # Cartridge Profiles
    # =========================================================================
    
    def get_profile(self, cartridge_id: str) -> Optional[CartridgeProfile]:
        """Get profile for a cartridge"""
        with self.cursor() as cur:
            cur.execute(
                "SELECT * FROM cartridge_profiles WHERE cartridge_id = ?",
                (cartridge_id,)
            )
            row = cur.fetchone()
            
            if not row:
                return None
            
            success_modes = json.loads(row["success_modes_json"] or "[]")
            failure_modes = json.loads(row["failure_modes_json"] or "[]")
            
            return CartridgeProfile(
                cartridge_id=row["cartridge_id"],
                signature_hash=row["signature_hash"] or "",
                success_modes=success_modes,
                failure_modes=failure_modes,
                last_seen=row["last_seen"] or "",
            )
    
    def get_profile_by_signature(self, signature_hash: str) -> Optional[CartridgeProfile]:
        """Get profile by signature hash (for similar cartridges)"""
        with self.cursor() as cur:
            cur.execute(
                "SELECT * FROM cartridge_profiles WHERE signature_hash = ? LIMIT 1",
                (signature_hash,)
            )
            row = cur.fetchone()
            
            if not row:
                return None
            
            success_modes = json.loads(row["success_modes_json"] or "[]")
            failure_modes = json.loads(row["failure_modes_json"] or "[]")
            
            return CartridgeProfile(
                cartridge_id=row["cartridge_id"],
                signature_hash=row["signature_hash"] or "",
                success_modes=success_modes,
                failure_modes=failure_modes,
                last_seen=row["last_seen"] or "",
            )
    
    def upsert_profile(self, 
                        cartridge_id: str,
                        signature_hash: str,
                        success_modes: Optional[List[str]] = None,
                        failure_modes: Optional[List[str]] = None):
        """Create or update a cartridge profile"""
        now = datetime.utcnow().isoformat()
        
        existing = self.get_profile(cartridge_id)
        
        if existing:
            # Merge modes
            all_success = list(set(existing.success_modes + (success_modes or [])))
            all_failure = list(set(existing.failure_modes + (failure_modes or [])))
            
            with self.cursor() as cur:
                cur.execute("""
                    UPDATE cartridge_profiles
                    SET signature_hash = ?,
                        success_modes_json = ?,
                        failure_modes_json = ?,
                        walk_count = walk_count + 1,
                        last_seen = ?
                    WHERE cartridge_id = ?
                """, (
                    signature_hash,
                    json.dumps(all_success),
                    json.dumps(all_failure),
                    now,
                    cartridge_id
                ))
        else:
            with self.cursor() as cur:
                cur.execute("""
                    INSERT INTO cartridge_profiles
                    (cartridge_id, signature_hash, success_modes_json, failure_modes_json, walk_count, last_seen)
                    VALUES (?, ?, ?, ?, 1, ?)
                """, (
                    cartridge_id,
                    signature_hash,
                    json.dumps(success_modes or []),
                    json.dumps(failure_modes or []),
                    now
                ))
    
    def add_success_mode(self, cartridge_id: str, mode: str):
        """Add a success mode to a profile"""
        profile = self.get_profile(cartridge_id)
        if profile:
            if mode not in profile.success_modes:
                profile.success_modes.append(mode)
                with self.cursor() as cur:
                    cur.execute("""
                        UPDATE cartridge_profiles
                        SET success_modes_json = ?
                        WHERE cartridge_id = ?
                    """, (json.dumps(profile.success_modes), cartridge_id))
    
    def add_failure_mode(self, cartridge_id: str, mode: str):
        """Add a failure mode to a profile"""
        profile = self.get_profile(cartridge_id)
        if profile:
            if mode not in profile.failure_modes:
                profile.failure_modes.append(mode)
                with self.cursor() as cur:
                    cur.execute("""
                        UPDATE cartridge_profiles
                        SET failure_modes_json = ?
                        WHERE cartridge_id = ?
                    """, (json.dumps(profile.failure_modes), cartridge_id))
