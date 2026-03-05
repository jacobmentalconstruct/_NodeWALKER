"""
Airlock Pane - Pre-Flight Checks.

Validates that all circuit highlighting requirements are met before
enabling the feature.
"""

from typing import List, Tuple, Optional
from dataclasses import dataclass


@dataclass
class CheckResult:
    """Result of a system check."""
    name: str
    passed: bool
    message: str


class AirlockPane:
    """
    Airlock Pane - validates circuit highlighting setup.
    """

    def __init__(self, session_db=None):
        """
        Initialize the airlock pane.

        Args:
            session_db: SessionDB instance to validate
        """
        self.session_db = session_db
        self.checks: List[CheckResult] = []

    def run_checks(self) -> bool:
        """
        Run all pre-flight checks.

        Returns:
            True if all checks pass, False otherwise.
        """
        self.checks.clear()

        # Check 1: Session DB is available
        self._check_session_db()

        # Check 2: Event bus is initialized
        self._check_event_bus()

        # Check 3: Tables exist and are readable
        self._check_tables()

        # All checks passed?
        return all(check.passed for check in self.checks)

    def _check_session_db(self) -> None:
        """Check that Session DB is writable."""
        if self.session_db is None:
            self.checks.append(CheckResult(
                name="Session Database",
                passed=False,
                message="Session DB not initialized"
            ))
            return

        try:
            # Try creating a test session
            test_session = self.session_db.create_session()
            self.checks.append(CheckResult(
                name="Session Database",
                passed=True,
                message=f"Session DB writable (test session: {test_session[:8]}...)"
            ))
        except Exception as e:
            self.checks.append(CheckResult(
                name="Session Database",
                passed=False,
                message=f"Session DB error: {str(e)}"
            ))

    def _check_event_bus(self) -> None:
        """Check that Event Bus is available."""
        try:
            from ..event_bus import get_event_bus
            bus = get_event_bus()
            self.checks.append(CheckResult(
                name="Event Bus",
                passed=True,
                message="Event bus initialized and ready"
            ))
        except Exception as e:
            self.checks.append(CheckResult(
                name="Event Bus",
                passed=False,
                message=f"Event bus error: {str(e)}"
            ))

    def _check_tables(self) -> None:
        """Check that all required tables exist."""
        if self.session_db is None:
            self.checks.append(CheckResult(
                name="Database Tables",
                passed=False,
                message="Cannot check tables - Session DB not available"
            ))
            return

        try:
            # Tables are created by ensure_schema()
            # Just verify we can query them
            conn = self.session_db._get_connection()
            cursor = conn.cursor()

            required_tables = ["queries", "activations", "summaries"]
            missing = []

            for table in required_tables:
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table,)
                )
                if not cursor.fetchone():
                    missing.append(table)

            conn.close()

            if missing:
                self.checks.append(CheckResult(
                    name="Database Tables",
                    passed=False,
                    message=f"Missing tables: {', '.join(missing)}"
                ))
            else:
                self.checks.append(CheckResult(
                    name="Database Tables",
                    passed=True,
                    message="All required tables exist"
                ))
        except Exception as e:
            self.checks.append(CheckResult(
                name="Database Tables",
                passed=False,
                message=f"Table check error: {str(e)}"
            ))

    def get_status(self) -> Tuple[bool, List[CheckResult]]:
        """Get current status and check results."""
        return all(check.passed for check in self.checks), self.checks

    def display_status(self) -> str:
        """Get a human-readable status report."""
        lines = ["Circuit Highlighting Requirements:"]
        lines.append("-" * 40)

        for check in self.checks:
            status = "[OK]" if check.passed else "[FAIL]"
            lines.append(f"{status} {check.name}: {check.message}")

        lines.append("-" * 40)
        all_passed = all(check.passed for check in self.checks)
        lines.append("Status: " + ("[OK] READY" if all_passed else "[FAIL] NOT READY"))

        return "\n".join(lines)
