"""
Evidence Gravity Engine.

Maintains the evidence bag and computes gravitational pull for candidates.
Heavy evidence (confirmed by multiple gradients) pulls the walk inward.
Light evidence (single gradient) is easy to drift past.

Gravity Formula:
    candidate_score = base_relevance + gravity_pull - distance_penalty - redundancy_penalty

Mass Formula:
    mass = sum(signals) + alignment_bonus * (gradient_count - 1)
    where gradient_count = number of non-zero signals
"""

from typing import Dict, List, Optional, Set, Tuple

from src.walker.gravity.types import (
    GravitySource, EvidenceMassScore, GravityConfig,
)


class EvidenceGravityEngine:
    """
    Tracks collected evidence mass and biases traversal toward
    meaning-dense regions of the datastore.

    The engine maintains a bag of GravitySources. Each source has mass
    computed from how many of the 4 gradients (structural, semantic,
    graph, verbatim) point to it. When scoring a new candidate, the
    engine computes gravity_pull as a weighted sum of proximity to
    heavy sources.
    """

    def __init__(self, config: Optional[GravityConfig] = None):
        self.config = config or GravityConfig()

        # Evidence bag: evidence_id -> GravitySource
        self._sources: Dict[str, GravitySource] = {}

        # Collected evidence IDs per facet
        self._facet_evidence: Dict[str, List[str]] = {}

    # =========================================================================
    # Evidence Registration
    # =========================================================================

    def register_evidence(
        self,
        evidence_id: str,
        target_type: str,
        facet_id: str = "",
        structural_signal: float = 0.0,
        semantic_signal: float = 0.0,
        graph_signal: float = 0.0,
        verbatim_signal: float = 0.0,
    ) -> GravitySource:
        """
        Register or update a piece of evidence in the bag.

        If the evidence already exists, signals are max-merged
        (each gradient keeps the highest observed signal).
        """
        if evidence_id in self._sources:
            src = self._sources[evidence_id]
            src.structural_signal = max(src.structural_signal, structural_signal)
            src.semantic_signal = max(src.semantic_signal, semantic_signal)
            src.graph_signal = max(src.graph_signal, graph_signal)
            src.verbatim_signal = max(src.verbatim_signal, verbatim_signal)
        else:
            src = GravitySource(
                evidence_id=evidence_id,
                target_type=target_type,
                structural_signal=structural_signal,
                semantic_signal=semantic_signal,
                graph_signal=graph_signal,
                verbatim_signal=verbatim_signal,
                facet_id=facet_id,
            )
            self._sources[evidence_id] = src

        # Recompute mass
        src.mass = self._compute_mass(src)

        # Track facet association
        if facet_id:
            bucket = self._facet_evidence.setdefault(facet_id, [])
            if evidence_id not in bucket:
                bucket.append(evidence_id)

        return src

    # =========================================================================
    # Mass Computation
    # =========================================================================

    def _compute_mass(self, source: GravitySource) -> float:
        """
        Compute gravitational mass for a source.

        mass = weighted_signal_sum + alignment_bonus * (gradient_count - 1)

        Alignment bonus rewards convergence: when 3+ gradients agree,
        the evidence is almost certainly core explanatory material.
        """
        cfg = self.config

        weighted_sum = (
            cfg.gravity_structural_weight * source.structural_signal
            + cfg.gravity_semantic_weight * source.semantic_signal
            + cfg.gravity_graph_weight * source.graph_signal
            + cfg.gravity_verbatim_weight * source.verbatim_signal
        )

        # Bonus for multi-gradient convergence
        n_gradients = source.gradient_count
        alignment = cfg.gradient_alignment_bonus * max(0, n_gradients - 1)

        return weighted_sum + alignment

    # =========================================================================
    # Gravity Pull Scoring
    # =========================================================================

    def score_candidate(
        self,
        candidate_id: str,
        base_relevance: float,
        walk_depth: int,
        structural_proximity: float = 0.0,
        semantic_proximity: float = 0.0,
        graph_proximity: float = 0.0,
        verbatim_overlap: float = 0.0,
        collected_ids: Optional[Set[str]] = None,
    ) -> EvidenceMassScore:
        """
        Score a candidate with gravity pull from heavy evidence.

        Args:
            candidate_id: ID of the candidate node/chunk
            base_relevance: Original score from the walker's Scorer
            walk_depth: Current depth in traversal (for distance penalty)
            structural_proximity: 0-1 proximity to heavy evidence (structural)
            semantic_proximity: 0-1 proximity to heavy evidence (semantic)
            graph_proximity: 0-1 proximity to heavy evidence (graph)
            verbatim_overlap: 0-1 identifier/literal overlap with heavy evidence
            collected_ids: Already-collected evidence IDs (for redundancy)

        Returns:
            EvidenceMassScore with gravity-adjusted total
        """
        collected_ids = collected_ids or set()

        # Compute gravity pull from heavy sources
        gravity_pull, contributing = self._compute_gravity_pull(
            structural_proximity=structural_proximity,
            semantic_proximity=semantic_proximity,
            graph_proximity=graph_proximity,
            verbatim_overlap=verbatim_overlap,
        )

        # Distance penalty: deeper walks get penalized
        distance_penalty = self._compute_distance_penalty(walk_depth)

        # Redundancy penalty: overlap with already-collected evidence
        redundancy_penalty = self._compute_redundancy_penalty(
            candidate_id, collected_ids
        )

        score = EvidenceMassScore(
            candidate_id=candidate_id,
            base_relevance=base_relevance,
            gravity_pull=gravity_pull,
            distance_penalty=distance_penalty,
            redundancy_penalty=redundancy_penalty,
            contributing_sources=contributing,
        )
        score.compute_total()
        return score

    def _compute_gravity_pull(
        self,
        structural_proximity: float,
        semantic_proximity: float,
        graph_proximity: float,
        verbatim_overlap: float,
    ) -> Tuple[float, List[str]]:
        """
        Compute total gravity pull from all heavy evidence sources.

        Each heavy source contributes pull proportional to:
        - its mass
        - the candidate's proximity along each gradient
        """
        cfg = self.config
        total_pull = 0.0
        contributing = []

        heavy_sources = self.get_heavy_sources()
        if not heavy_sources:
            return 0.0, []

        for src in heavy_sources:
            # Proximity-weighted pull from this source
            pull = (
                cfg.gravity_structural_weight * structural_proximity * src.structural_signal
                + cfg.gravity_semantic_weight * semantic_proximity * src.semantic_signal
                + cfg.gravity_graph_weight * graph_proximity * src.graph_signal
                + cfg.gravity_verbatim_weight * verbatim_overlap * src.verbatim_signal
            )

            # Scale by source mass
            pull *= src.mass

            if pull > 0.01:
                total_pull += pull
                contributing.append(src.evidence_id)

        # Normalize: average pull per heavy source, capped at 1.0
        if heavy_sources:
            total_pull /= len(heavy_sources)

        return min(total_pull, 1.0), contributing

    def _compute_distance_penalty(self, walk_depth: int) -> float:
        """
        Penalty increases with walk depth.
        Decay: 0.15 * depth (linear, max 0.6 at depth 4).
        """
        max_depth = self.config.max_walk_depth
        if walk_depth <= 0:
            return 0.0
        return min(0.15 * walk_depth, 0.15 * max_depth)

    def _compute_redundancy_penalty(
        self,
        candidate_id: str,
        collected_ids: Set[str],
    ) -> float:
        """
        Penalty for candidates that overlap with already-collected evidence.
        Returns 0.5 if already collected, 0.0 otherwise.
        """
        if candidate_id in collected_ids:
            return 0.5
        return 0.0

    # =========================================================================
    # Query Methods
    # =========================================================================

    def get_heavy_sources(self) -> List[GravitySource]:
        """Get all sources with mass above the heavy threshold."""
        threshold = self.config.heavy_mass_threshold
        return [
            s for s in self._sources.values()
            if s.mass >= threshold
        ]

    def get_sources_for_facet(self, facet_id: str) -> List[GravitySource]:
        """Get all evidence sources associated with a facet."""
        ids = self._facet_evidence.get(facet_id, [])
        return [self._sources[eid] for eid in ids if eid in self._sources]

    def get_heavy_sources_for_facet(self, facet_id: str) -> List[GravitySource]:
        """Get heavy sources for a specific facet."""
        threshold = self.config.heavy_mass_threshold
        return [
            s for s in self.get_sources_for_facet(facet_id)
            if s.mass >= threshold
        ]

    def get_all_evidence_ids(self) -> Set[str]:
        """Get all registered evidence IDs."""
        return set(self._sources.keys())

    def get_source(self, evidence_id: str) -> Optional[GravitySource]:
        """Get a single source by ID."""
        return self._sources.get(evidence_id)

    def evidence_count(self) -> int:
        return len(self._sources)

    def heavy_count(self) -> int:
        return len(self.get_heavy_sources())

    # =========================================================================
    # Lifecycle
    # =========================================================================

    def reset(self):
        """Clear all evidence for a new query."""
        self._sources.clear()
        self._facet_evidence.clear()

    def reset_facet(self, facet_id: str):
        """Clear evidence for a single facet (keeps other facets)."""
        ids_to_remove = self._facet_evidence.pop(facet_id, [])
        for eid in ids_to_remove:
            # Only remove if not claimed by another facet
            still_claimed = any(
                eid in eids
                for fid, eids in self._facet_evidence.items()
            )
            if not still_claimed:
                self._sources.pop(eid, None)
