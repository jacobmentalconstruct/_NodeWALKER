"""
Anti-Data Module
Rule engine for blocking, penalizing, or warning on known-bad patterns.

Rules can match on:
- exact: Exact string match
- prefix: String prefix match
- regex: Regular expression
- cid_set: Comma-separated list of CIDs

Actions:
- block: Exclude candidate entirely
- penalize: Subtract penalty from score
- warn: Allow but flag for review
"""

import re
from typing import List, Dict, Set, Optional, Tuple
from dataclasses import dataclass

from src.walker.types import AntiDataRule, AntiDataAction
from src.walker.notes import NotesDB


@dataclass
class RuleMatch:
    """Result of a rule match"""
    rule: AntiDataRule
    matched_value: str
    action: AntiDataAction
    penalty: float
    reason: str


@dataclass
class AntiDataResult:
    """Combined result of anti-data evaluation"""
    blocked: bool = False
    total_penalty: float = 0.0
    matches: List[RuleMatch] = None
    warnings: List[str] = None
    
    def __post_init__(self):
        if self.matches is None:
            self.matches = []
        if self.warnings is None:
            self.warnings = []


class AntiDataEngine:
    """
    Engine for evaluating anti-data rules against candidates.
    
    Usage:
        engine = AntiDataEngine(notes_db)
        engine.load_rules()
        result = engine.evaluate("some_chunk_id")
        if result.blocked:
            # Skip this candidate
        else:
            score -= result.total_penalty
    """
    
    def __init__(self, notes_db: Optional[NotesDB] = None):
        self.notes_db = notes_db
        
        # Cached rules by type
        self._exact_rules: Dict[str, AntiDataRule] = {}
        self._prefix_rules: List[AntiDataRule] = []
        self._regex_rules: List[Tuple[re.Pattern, AntiDataRule]] = []
        self._cid_set_rules: List[Tuple[Set[str], AntiDataRule]] = []
        
        self._loaded = False
    
    def load_rules(self):
        """Load rules from notes database"""
        if not self.notes_db or not self.notes_db.is_connected:
            self._loaded = True
            return
        
        rules = self.notes_db.get_rules()
        
        self._exact_rules.clear()
        self._prefix_rules.clear()
        self._regex_rules.clear()
        self._cid_set_rules.clear()
        
        for rule in rules:
            self._index_rule(rule)
        
        self._loaded = True
    
    def _index_rule(self, rule: AntiDataRule):
        """Index a rule by its match type"""
        if rule.match_type == "exact":
            self._exact_rules[rule.match_value] = rule
        
        elif rule.match_type == "prefix":
            self._prefix_rules.append(rule)
        
        elif rule.match_type == "regex":
            try:
                pattern = re.compile(rule.match_value)
                self._regex_rules.append((pattern, rule))
            except re.error:
                pass  # Invalid regex, skip
        
        elif rule.match_type == "cid_set":
            cids = set(c.strip() for c in rule.match_value.split(","))
            self._cid_set_rules.append((cids, rule))
    
    def evaluate(self, value: str) -> AntiDataResult:
        """
        Evaluate a value against all rules.
        
        Args:
            value: The value to check (chunk_id, node_id, file_cid, etc.)
        
        Returns:
            AntiDataResult with block status, penalties, and matches
        """
        if not self._loaded:
            self.load_rules()
        
        result = AntiDataResult()
        
        # Check exact matches (O(1))
        if value in self._exact_rules:
            rule = self._exact_rules[value]
            self._apply_match(result, rule, value)
        
        # Check prefix matches (O(n))
        for rule in self._prefix_rules:
            if value.startswith(rule.match_value):
                self._apply_match(result, rule, value)
        
        # Check regex matches (O(n))
        for pattern, rule in self._regex_rules:
            if pattern.search(value):
                self._apply_match(result, rule, value)
        
        # Check CID set matches (O(n * m))
        for cid_set, rule in self._cid_set_rules:
            if value in cid_set:
                self._apply_match(result, rule, value)
        
        return result
    
    def _apply_match(self, result: AntiDataResult, 
                      rule: AntiDataRule, value: str):
        """Apply a rule match to the result"""
        match = RuleMatch(
            rule=rule,
            matched_value=value,
            action=rule.action,
            penalty=rule.penalty,
            reason=rule.reason,
        )
        result.matches.append(match)
        
        if rule.action == AntiDataAction.BLOCK:
            result.blocked = True
        elif rule.action == AntiDataAction.PENALIZE:
            result.total_penalty += rule.penalty
        elif rule.action == AntiDataAction.WARN:
            result.warnings.append(rule.reason or f"Warning: {rule.match_value}")
    
    def evaluate_batch(self, values: List[str]) -> Dict[str, AntiDataResult]:
        """Evaluate multiple values"""
        return {v: self.evaluate(v) for v in values}
    
    # =========================================================================
    # Rule Management (convenience methods)
    # =========================================================================
    
    def add_block_rule(self, match_value: str,
                        match_type: str = "exact",
                        reason: str = "") -> Optional[str]:
        """Add a blocking rule"""
        if not self.notes_db:
            return None
        
        rule_id = self.notes_db.add_rule(
            match_type=match_type,
            match_value=match_value,
            action=AntiDataAction.BLOCK,
            penalty=0.0,
            reason=reason,
        )
        
        # Reload rules
        self.load_rules()
        
        return rule_id
    
    def add_penalty_rule(self, match_value: str,
                          penalty: float,
                          match_type: str = "exact",
                          reason: str = "") -> Optional[str]:
        """Add a penalty rule"""
        if not self.notes_db:
            return None
        
        rule_id = self.notes_db.add_rule(
            match_type=match_type,
            match_value=match_value,
            action=AntiDataAction.PENALIZE,
            penalty=penalty,
            reason=reason,
        )
        
        self.load_rules()
        
        return rule_id
    
    def add_warning_rule(self, match_value: str,
                          match_type: str = "exact",
                          reason: str = "") -> Optional[str]:
        """Add a warning rule"""
        if not self.notes_db:
            return None
        
        rule_id = self.notes_db.add_rule(
            match_type=match_type,
            match_value=match_value,
            action=AntiDataAction.WARN,
            penalty=0.0,
            reason=reason,
        )
        
        self.load_rules()
        
        return rule_id
    
    def remove_rule(self, rule_id: str):
        """Remove a rule"""
        if self.notes_db:
            self.notes_db.remove_rule(rule_id)
            self.load_rules()
    
    # =========================================================================
    # Common Anti-Patterns
    # =========================================================================
    
    def block_pipeline_version(self, version: str, reason: str = ""):
        """Block a specific pipeline version"""
        return self.add_block_rule(
            match_value=f"pipeline_ver:{version}",
            match_type="exact",
            reason=reason or f"Pipeline version {version} is untrusted"
        )
    
    def penalize_edge_type(self, edge_type: str, penalty: float, reason: str = ""):
        """Penalize a specific edge type"""
        return self.add_penalty_rule(
            match_value=f"edge_type:{edge_type}",
            match_type="exact",
            penalty=penalty,
            reason=reason or f"Edge type {edge_type} is unreliable"
        )
    
    def block_file_pattern(self, pattern: str, reason: str = ""):
        """Block files matching a regex pattern"""
        return self.add_block_rule(
            match_value=pattern,
            match_type="regex",
            reason=reason or f"Files matching {pattern} are blocked"
        )
    
    # =========================================================================
    # Stats
    # =========================================================================
    
    def get_stats(self) -> Dict[str, int]:
        """Get rule count statistics"""
        return {
            "exact_rules": len(self._exact_rules),
            "prefix_rules": len(self._prefix_rules),
            "regex_rules": len(self._regex_rules),
            "cid_set_rules": len(self._cid_set_rules),
            "total_rules": (
                len(self._exact_rules) +
                len(self._prefix_rules) +
                len(self._regex_rules) +
                len(self._cid_set_rules)
            ),
        }
