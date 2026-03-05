"""
Sufficiency Critic.

Evaluates whether the walker has collected enough evidence to answer
the query, both per-facet and globally. Implements the "enough evidence"
gate that prevents infinite wandering.

Two evaluation layers:
  Layer 1 - Per facet: Stop walking a facet when evidence is sufficient.
  Layer 2 - Global: Stop the whole collection when all facets answerable
            OR KV budget is exhausted.
"""

from typing import List, Optional

from src.walker.gravity.types import (
    Facet, GravityConfig, GravitySource,
    SufficiencyLevel, FacetSufficiency, SufficiencyReport,
)
from src.walker.gravity.engine import EvidenceGravityEngine


class SufficiencyCritic:
    """
    Determines when the walker has collected enough evidence.

    Checks evidence sufficiency at two layers:
    - Per-facet: does each sub-question have enough support?
    - Global: can we stop collecting entirely?
    """

    def __init__(
        self,
        gravity_engine: EvidenceGravityEngine,
        config: Optional[GravityConfig] = None,
    ):
        self.engine = gravity_engine
        self.config = config or GravityConfig()

    # =========================================================================
    # Per-Facet Sufficiency (Layer 1)
    # =========================================================================

    def evaluate_facet(self, facet: Facet) -> FacetSufficiency:
        """
        Evaluate evidence sufficiency for a single facet.

        Sufficient when:
        - evidence_count >= min_evidence_per_facet AND
        - heavy_evidence_count >= min_heavy_evidence_per_facet
        """
        cfg = self.config
        sources = self.engine.get_sources_for_facet(facet.facet_id)
        heavy = self.engine.get_heavy_sources_for_facet(facet.facet_id)

        evidence_count = len(sources)
        heavy_count = len(heavy)

        # Compute coverage (0-1)
        if cfg.min_evidence_per_facet > 0:
            coverage = min(1.0, evidence_count / cfg.min_evidence_per_facet)
        else:
            coverage = 1.0 if evidence_count > 0 else 0.0

        # Determine level
        if (evidence_count >= cfg.min_evidence_per_facet
                and heavy_count >= cfg.min_heavy_evidence_per_facet):
            level = SufficiencyLevel.SUFFICIENT
        elif evidence_count > 0:
            level = SufficiencyLevel.PARTIAL
        else:
            level = SufficiencyLevel.INSUFFICIENT

        return FacetSufficiency(
            facet_id=facet.facet_id,
            level=level,
            evidence_count=evidence_count,
            heavy_evidence_count=heavy_count,
            coverage=coverage,
        )

    def is_facet_sufficient(self, facet: Facet) -> bool:
        """Quick check: is this facet done?"""
        result = self.evaluate_facet(facet)
        return result.level == SufficiencyLevel.SUFFICIENT

    # =========================================================================
    # Global Sufficiency (Layer 2)
    # =========================================================================

    def evaluate_global(
        self,
        facets: List[Facet],
        tokens_used: int = 0,
    ) -> SufficiencyReport:
        """
        Evaluate global sufficiency across all facets.

        Stop when ANY of:
        - All facets are answerable
        - KV budget is exhausted
        - All facets have at least partial evidence and frontier is cold
        """
        cfg = self.config
        kv_remaining = max(0, cfg.kv_token_budget - tokens_used)

        facet_results = [self.evaluate_facet(f) for f in facets]

        # Check if all facets are answerable
        all_answerable = all(fr.is_answerable for fr in facet_results)

        # Check if all facets are sufficient
        all_sufficient = all(
            fr.level == SufficiencyLevel.SUFFICIENT for fr in facet_results
        )

        # Average coverage
        if facet_results:
            avg_coverage = sum(fr.coverage for fr in facet_results) / len(facet_results)
        else:
            avg_coverage = 0.0

        # Determine global level
        if all_sufficient:
            global_level = SufficiencyLevel.SUFFICIENT
        elif all_answerable or avg_coverage >= cfg.sufficiency_coverage_threshold:
            global_level = SufficiencyLevel.PARTIAL
        else:
            global_level = SufficiencyLevel.INSUFFICIENT

        # Should we stop?
        should_stop = False
        reason = ""

        if all_sufficient:
            should_stop = True
            reason = "all facets have sufficient evidence"
        elif kv_remaining <= 0:
            should_stop = True
            reason = "kv budget exhausted"
        elif all_answerable and avg_coverage >= cfg.sufficiency_coverage_threshold:
            should_stop = True
            reason = "all facets answerable with adequate coverage"

        return SufficiencyReport(
            facets=facet_results,
            global_level=global_level,
            all_facets_answerable=all_answerable,
            kv_budget_remaining=kv_remaining,
            should_stop=should_stop,
            reason=reason,
        )

    # =========================================================================
    # Termination Conditions (5 stop rules)
    # =========================================================================

    def check_termination(
        self,
        facets: List[Facet],
        walk_depth: int,
        recent_scores: List[float],
        tokens_used: int,
        elapsed_ms: int,
        nodes_visited: int,
    ) -> tuple:
        """
        Check all 5 termination conditions.

        Returns (should_stop: bool, reason: str)

        Conditions:
        1. Evidence sufficiency (primary)
        2. Intent satisfaction (all facets answerable)
        3. Traversal depth limit
        4. Relevance decay (frontier going cold)
        5. Budget exhaustion (KV, nodes, time)
        """
        cfg = self.config

        # 1. Evidence sufficiency
        report = self.evaluate_global(facets, tokens_used)
        if report.should_stop:
            return True, f"sufficiency: {report.reason}"

        # 2. Intent satisfaction (checked inside evaluate_global)
        # Already handled above

        # 3. Traversal depth limit
        if walk_depth >= cfg.max_walk_depth:
            return True, f"depth limit reached ({walk_depth})"

        # 4. Relevance decay
        if self._is_frontier_cold(recent_scores):
            return True, "relevance decay: frontier scores below threshold"

        # 5. Budget exhaustion
        if tokens_used >= cfg.kv_token_budget:
            return True, "kv token budget exhausted"
        if nodes_visited >= cfg.max_nodes_per_facet:
            return True, f"node budget exhausted ({nodes_visited})"
        if elapsed_ms >= cfg.max_time_per_facet_ms:
            return True, f"time budget exhausted ({elapsed_ms}ms)"

        return False, ""

    def _is_frontier_cold(self, recent_scores: List[float]) -> bool:
        """
        Check if the frontier has gone cold (relevance decay).

        Cold when the last 5 candidate scores average below threshold.
        """
        if len(recent_scores) < 5:
            return False

        avg = sum(recent_scores[-5:]) / 5
        return avg < self.config.relevance_decay_threshold
