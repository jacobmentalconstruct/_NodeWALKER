"""
Forensic Query Pipeline.

Resolves referents, classifies scope/intent, and dispatches to gravity.
"""

from src.walker.forensics.types import (
    ScopeLabel,
    ReferentType,
    IntentLabel,
    ReferentBinding,
    ManifoldResult,
    FacetResult,
    SufficiencySummary,
)
from src.walker.forensics.referents import resolve_active_referent
from src.walker.forensics.router import classify_scope, classify_intent
from src.walker.forensics.pipeline import run_forensic_query

__all__ = [
    "ScopeLabel", "ReferentType", "IntentLabel",
    "ReferentBinding", "ManifoldResult", "FacetResult", "SufficiencySummary",
    "resolve_active_referent", "classify_scope", "classify_intent",
    "run_forensic_query",
]
