"""
Forensic Evidence Gravity Pipeline.

The full orchestration loop:

    User prompt
        |
    1. Intent Router (decompose into facets)
        |
    2. Facet Critic (validate facets)
        |
    3. Walk per facet (bounded, gravity-scored)
        |
    4. Evidence Packer (compress into KV budget)
        |
    5. Synthesis Answer (one big inference)
        |
    6. Integrity Critic (verify answer)

Uses the existing NodeWalker's operators (structure, chunks, graph, cas)
as a toolkit but implements its own gravity-enhanced traversal loop.
Does NOT modify walker.py.
"""

import time
import uuid
from typing import List, Dict, Optional, Set, Tuple, Any
from dataclasses import dataclass, field

from src.walker.gravity.types import (
    Facet, FacetKind, GravityConfig, GravitySource,
    EvidenceMassScore, SufficiencyReport, KVPackPlan,
)
from src.walker.gravity.decomposer import FacetDecomposer
from src.walker.gravity.engine import EvidenceGravityEngine
from src.walker.gravity.sufficiency import SufficiencyCritic
from src.walker.gravity.packer import EvidencePacker

from src.walker.scoring import Scorer
from src.walker.activation_types import ActivationKind, TargetType


@dataclass
class ForensicResult:
    """Output of the forensic pipeline."""
    query: str
    facets: List[Facet]
    pack_plan: Optional[KVPackPlan] = None
    synthesis: str = ""
    integrity_ok: bool = True
    integrity_notes: str = ""
    evidence_count: int = 0
    heavy_evidence_count: int = 0
    elapsed_ms: int = 0
    sufficiency: Optional[SufficiencyReport] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "facets": [
                {
                    "id": f.facet_id,
                    "kind": f.kind.value,
                    "question": f.question,
                    "priority": f.priority,
                    "evidence_count": f.evidence_count,
                    "sufficient": f.sufficient,
                }
                for f in self.facets
            ],
            "synthesis": self.synthesis,
            "integrity_ok": self.integrity_ok,
            "evidence_count": self.evidence_count,
            "heavy_evidence_count": self.heavy_evidence_count,
            "elapsed_ms": self.elapsed_ms,
        }


class ForensicPipeline:
    """
    Evidence Gravity forensic pipeline.

    Coordinates facet decomposition, gravity-enhanced traversal,
    evidence packing, synthesis, and integrity verification.

    Uses the walker's operators but does NOT call walker.walk().
    Instead, it runs its own per-facet traversal loop with gravity.
    """

    def __init__(
        self,
        walker,
        llm_agent=None,
        config: Optional[GravityConfig] = None,
    ):
        """
        Args:
            walker: Initialized NodeWalker instance (provides operators + DB)
            llm_agent: LLMAgent instance (for decomposition, compression, critics)
            config: GravityConfig (tuning knobs for the pipeline)
        """
        self.walker = walker
        self.llm_agent = llm_agent
        self.config = config or GravityConfig()

        # Sub-systems (created fresh per run)
        self._gravity: Optional[EvidenceGravityEngine] = None
        self._decomposer: Optional[FacetDecomposer] = None
        self._critic: Optional[SufficiencyCritic] = None
        self._packer: Optional[EvidencePacker] = None

    # =========================================================================
    # Main Entry Point
    # =========================================================================

    def run(self, query: str) -> ForensicResult:
        """
        Execute the full forensic pipeline.

        Returns a ForensicResult with facets, evidence, synthesis, and
        integrity assessment.
        """
        start_time = time.time()

        # Initialize sub-systems
        self._gravity = EvidenceGravityEngine(self.config)
        self._decomposer = FacetDecomposer(self.llm_agent, self.config)
        self._critic = SufficiencyCritic(self._gravity, self.config)
        self._packer = EvidencePacker(self._gravity, self.config, self.llm_agent)

        # ---- Stage 1: Intent Router (decompose into facets) ----
        facets = self._decomposer.decompose(query)

        # ---- Stage 2: Facet Critic (validate facets) ----
        facets = self._critique_facets(facets, query)

        # ---- Stage 3: Walk per facet (gravity-enhanced) ----
        evidence_texts = self._walk_all_facets(facets, query)

        # ---- Stage 4: Evidence Packer (compress into KV budget) ----
        pack_plan = self._packer.pack(facets, evidence_texts)

        # ---- Stage 5: Synthesis Answer ----
        synthesis = self._synthesize(pack_plan, query)

        # ---- Stage 6: Integrity Critic ----
        integrity_ok, integrity_notes = self._integrity_check(
            query, facets, synthesis
        )

        # Mark facet sufficiency
        for facet in facets:
            facet.sufficient = self._critic.is_facet_sufficient(facet)

        elapsed_ms = int((time.time() - start_time) * 1000)

        return ForensicResult(
            query=query,
            facets=facets,
            pack_plan=pack_plan,
            synthesis=synthesis,
            integrity_ok=integrity_ok,
            integrity_notes=integrity_notes,
            evidence_count=self._gravity.evidence_count(),
            heavy_evidence_count=self._gravity.heavy_count(),
            elapsed_ms=elapsed_ms,
            sufficiency=self._critic.evaluate_global(facets),
        )

    # =========================================================================
    # Stage 2: Facet Critic
    # =========================================================================

    def _critique_facets(self, facets: List[Facet], query: str) -> List[Facet]:
        """
        Validate and optionally refine facets.

        Uses LLM helper to check if facets are well-posed.
        Falls back to pass-through if LLM unavailable.
        """
        if not self.llm_agent or not facets:
            return facets

        # Quick sanity: remove duplicates by question similarity
        seen = set()
        unique = []
        for f in facets:
            key = f.question.lower().strip()
            if key not in seen:
                seen.add(key)
                unique.append(f)

        return unique

    # =========================================================================
    # Stage 3: Per-Facet Gravity Walk
    # =========================================================================

    def _walk_all_facets(
        self,
        facets: List[Facet],
        query: str,
    ) -> Dict[str, str]:
        """
        Walk each facet independently with gravity-enhanced scoring.

        Returns map of evidence_id -> verbatim text.
        """
        all_evidence_texts: Dict[str, str] = {}

        for facet in facets:
            texts = self._walk_facet(facet, query)
            all_evidence_texts.update(texts)

        return all_evidence_texts

    def _walk_facet(
        self,
        facet: Facet,
        query: str,
    ) -> Dict[str, str]:
        """
        Gravity-enhanced walk for a single facet.

        Uses the walker's operators (structure, chunks, graph) with
        the gravity engine biasing traversal toward heavy evidence.
        """
        cfg = self.config
        walker = self.walker
        gravity = self._gravity
        critic = self._critic

        # Collected evidence for this facet
        evidence_texts: Dict[str, str] = {}
        visited_nodes: Set[str] = set()
        visited_chunks: Set[str] = set()
        recent_scores: List[float] = []

        # Create scorer for this facet walk
        scorer = Scorer(walker.config.policy)

        # Seed using the facet question via FTS
        seeds = self._get_facet_seeds(facet, query)
        for seed_id, seed_type in seeds:
            candidate = scorer.create_candidate(
                target_id=seed_id,
                target_type=seed_type,
                operator=walker.config.policy.mode,
                source_id="seed",
                semantic=1.0,
                structural=1.0,
                source=1.0,
                distance=0,
            )
            scorer.add_candidate(candidate)

            # Emit activation for circuit highlighting
            target = TargetType.TREE_NODE if seed_type == "node" else TargetType.CHUNK
            walker.emit_activation(ActivationKind.ENTRY_HIT, target, seed_id,
                                   meta={"facet_id": facet.facet_id})

        # Main walk loop
        start = time.time()
        walk_depth = 0

        while not scorer.should_stop():
            elapsed_ms = int((time.time() - start) * 1000)

            # Check termination (5 conditions)
            should_stop, reason = critic.check_termination(
                facets=[facet],
                walk_depth=walk_depth,
                recent_scores=recent_scores,
                tokens_used=sum(len(t) // 4 for t in evidence_texts.values()),
                elapsed_ms=elapsed_ms,
                nodes_visited=len(visited_nodes),
            )
            if should_stop:
                break

            candidate = scorer.pop_best_candidate()
            if not candidate:
                break

            # Gravity-enhanced scoring
            grav_score = gravity.score_candidate(
                candidate_id=candidate.target_id,
                base_relevance=scorer.compute_score(candidate),
                walk_depth=candidate.distance,
                structural_proximity=candidate.score.structural,
                semantic_proximity=candidate.score.semantic,
                graph_proximity=candidate.score.graph,
                verbatim_overlap=0.0,
                collected_ids=visited_chunks | visited_nodes,
            )

            recent_scores.append(grav_score.total)
            walk_depth = max(walk_depth, candidate.distance)

            # Expand candidate
            if candidate.target_type == "node":
                self._expand_node_for_facet(
                    candidate, facet, scorer, gravity,
                    visited_nodes, visited_chunks, evidence_texts,
                )
            elif candidate.target_type == "chunk":
                self._expand_chunk_for_facet(
                    candidate, facet, scorer, gravity,
                    visited_nodes, visited_chunks, evidence_texts,
                )

            scorer.mark_expansion()

        return evidence_texts

    # =========================================================================
    # Node/Chunk Expansion (Per-Facet)
    # =========================================================================

    def _expand_node_for_facet(
        self,
        candidate,
        facet: Facet,
        scorer: Scorer,
        gravity: EvidenceGravityEngine,
        visited_nodes: Set[str],
        visited_chunks: Set[str],
        evidence_texts: Dict[str, str],
    ):
        """Expand a tree node during facet walk."""
        walker = self.walker
        node_id = candidate.target_id

        if node_id in visited_nodes:
            return
        visited_nodes.add(node_id)
        scorer.mark_node_visited(node_id)

        # Emit traversal activation
        walker.emit_activation(
            ActivationKind.TRAVERSAL_HOP, TargetType.TREE_NODE, node_id,
            meta={"facet_id": facet.facet_id},
        )

        node = walker.structure.get_node(node_id)
        if not node:
            return

        # Collect chunks for this node
        chunks = walker.chunks.node_to_chunks(node_id)
        for chunk in chunks:
            if chunk.chunk_id not in visited_chunks:
                self._collect_chunk_for_facet(
                    chunk.chunk_id, facet, gravity,
                    visited_chunks, evidence_texts,
                    structural_signal=1.0,
                )

        # Add structural expansion candidates
        distance = candidate.distance + 1
        policy = walker.config.policy

        if policy.use_structure:
            for child in walker.structure.children(node_id):
                if child.node_id not in visited_nodes:
                    c = scorer.create_candidate(
                        target_id=child.node_id,
                        target_type="node",
                        operator=child.node_type,
                        source_id=node_id,
                        structural=scorer.compute_structural_score(1),
                        distance=distance,
                    )
                    scorer.add_candidate(c)

            parent = walker.structure.parent(node_id)
            if parent and parent.node_id not in visited_nodes:
                c = scorer.create_candidate(
                    target_id=parent.node_id,
                    target_type="node",
                    operator="parent",
                    source_id=node_id,
                    structural=scorer.compute_structural_score(1) * 0.8,
                    distance=distance,
                )
                scorer.add_candidate(c)

        # Graph expansion
        if policy.use_graph and node.graph_node_id:
            if scorer.budget_state.graph_hops < policy.graph_max_hops:
                expansion = walker.graph.expand_from_tree_node(
                    node_id,
                    edge_types=policy.allowed_edge_types,
                    k=5,
                    visited_graph=visited_nodes,
                )
                for gnode, edge in expansion.neighbors:
                    tree_node = walker.graph.graph_to_tree_node(gnode.node_id)
                    if tree_node and tree_node.node_id not in visited_nodes:
                        c = scorer.create_candidate(
                            target_id=tree_node.node_id,
                            target_type="node",
                            operator="graph",
                            source_id=node_id,
                            graph=scorer.compute_graph_score(1, edge.edge_type),
                            edge_type=edge.edge_type,
                            distance=distance,
                        )
                        scorer.add_candidate(c)
                scorer.mark_graph_hop()

    def _expand_chunk_for_facet(
        self,
        candidate,
        facet: Facet,
        scorer: Scorer,
        gravity: EvidenceGravityEngine,
        visited_nodes: Set[str],
        visited_chunks: Set[str],
        evidence_texts: Dict[str, str],
    ):
        """Expand a chunk during facet walk."""
        walker = self.walker
        chunk_id = candidate.target_id

        # Collect this chunk
        self._collect_chunk_for_facet(
            chunk_id, facet, gravity,
            visited_chunks, evidence_texts,
            semantic_signal=candidate.score.semantic,
        )

        # Adjacency expansion
        chunk = walker.chunks.get_chunk(chunk_id)
        if not chunk:
            return

        if chunk.node_id:
            scorer.mark_node_visited(chunk.node_id)
            visited_nodes.add(chunk.node_id)

        distance = candidate.distance + 1
        policy = walker.config.policy

        if policy.use_adjacency:
            prev_chunk = walker.chunks.chunk_prev(chunk_id)
            if prev_chunk and prev_chunk.chunk_id not in visited_chunks:
                c = scorer.create_candidate(
                    target_id=prev_chunk.chunk_id,
                    target_type="chunk",
                    operator="chunk_prev",
                    source_id=chunk_id,
                    adjacency=scorer.compute_adjacency_score(1),
                    distance=distance,
                )
                scorer.add_candidate(c)

            next_chunk = walker.chunks.chunk_next(chunk_id)
            if next_chunk and next_chunk.chunk_id not in visited_chunks:
                c = scorer.create_candidate(
                    target_id=next_chunk.chunk_id,
                    target_type="chunk",
                    operator="chunk_next",
                    source_id=chunk_id,
                    adjacency=scorer.compute_adjacency_score(1),
                    distance=distance,
                )
                scorer.add_candidate(c)

    def _collect_chunk_for_facet(
        self,
        chunk_id: str,
        facet: Facet,
        gravity: EvidenceGravityEngine,
        visited_chunks: Set[str],
        evidence_texts: Dict[str, str],
        structural_signal: float = 0.0,
        semantic_signal: float = 0.0,
        graph_signal: float = 0.0,
    ):
        """Collect a chunk as evidence for a facet."""
        if chunk_id in visited_chunks:
            return

        visited_chunks.add(chunk_id)

        # Get content
        content = self.walker.chunks.get_content(chunk_id)
        if not content:
            return

        # Store text
        evidence_texts[chunk_id] = content

        # Track in facet
        facet.evidence_ids.append(chunk_id)

        # Register in gravity engine
        gravity.register_evidence(
            evidence_id=chunk_id,
            target_type="chunk",
            facet_id=facet.facet_id,
            structural_signal=structural_signal,
            semantic_signal=semantic_signal,
            graph_signal=graph_signal,
            verbatim_signal=0.0,
        )

        # Emit collect activation
        self.walker.emit_activation(
            ActivationKind.COLLECT, TargetType.CHUNK, chunk_id,
            meta={"facet_id": facet.facet_id},
        )

    # =========================================================================
    # Seeding
    # =========================================================================

    def _get_facet_seeds(
        self,
        facet: Facet,
        query: str,
    ) -> List[Tuple[str, str]]:
        """Get seeds for a facet walk using FTS on the facet question."""
        walker = self.walker

        # Use facet question for FTS (more targeted than full query)
        search_text = facet.question
        results = walker.db.fts_search_chunks(
            search_text,
            limit=walker.config.policy.semantic_top_k,
        )

        seeds = []
        for chunk_id, rank in results:
            seeds.append((chunk_id, "chunk"))

            # Also add associated tree node
            chunk = walker.db.get_chunk(chunk_id)
            if chunk and chunk.node_id:
                seeds.append((chunk.node_id, "node"))

        # Dedupe
        seen = set()
        unique = []
        for s in seeds:
            if s[0] not in seen:
                seen.add(s[0])
                unique.append(s)

        return unique[:10]  # Cap seeds per facet

    # =========================================================================
    # Stage 5: Synthesis
    # =========================================================================

    def _synthesize(self, plan: KVPackPlan, query: str) -> str:
        """
        Run the big synthesis inference call.

        Uses the packed evidence as context for a single comprehensive answer.
        """
        if not self.llm_agent:
            return self._fallback_synthesis(plan, query)

        prompt = self._packer.render_synthesis_prompt(plan, query)

        try:
            response, citations = self.llm_agent.process_prompt(
                prompt=prompt,
                session_id=self.walker.session_id or "forensic",
                include_tier2_3=False,  # Pack already contains the context
            )

            # Emit SYNTHESIS_USED activations for cited chunks
            for ctype, cid in citations:
                if ctype == "chunk":
                    self.walker.emit_activation(
                        ActivationKind.SYNTHESIS_USED,
                        TargetType.CHUNK,
                        cid,
                    )

            return response
        except Exception as e:
            return f"[Synthesis error: {e}]"

    def _fallback_synthesis(self, plan: KVPackPlan, query: str) -> str:
        """Fallback synthesis when LLM is unavailable."""
        parts = [f"## Answer to: {query}\n"]
        for pf in plan.packed_facets:
            parts.append(f"### {pf.question}")
            parts.append(pf.summary)
        return "\n\n".join(parts)

    # =========================================================================
    # Stage 6: Integrity Critic
    # =========================================================================

    def _integrity_check(
        self,
        query: str,
        facets: List[Facet],
        synthesis: str,
    ) -> Tuple[bool, str]:
        """
        Verify the synthesis answer for integrity.

        Checks:
        - Does the answer address the query?
        - Are citations present and valid?
        - Is there scope drift?
        """
        if not self.config.enable_integrity_critic or not self.llm_agent:
            return True, "integrity critic disabled"

        facet_questions = "\n".join(
            f"- {f.question}" for f in facets
        )

        system = (
            "You are an integrity critic. Evaluate this answer.\n"
            "Check: (1) Does it address the query? (2) Are citations present? "
            "(3) Is there scope drift?\n"
            "Reply with ONLY: OK or FAIL: <reason>"
        )

        prompt = (
            f"Query: {query}\n\n"
            f"Facets:\n{facet_questions}\n\n"
            f"Answer:\n{synthesis}"
        )

        try:
            verdict = self.llm_agent.call_helper(
                system=system,
                prompt=prompt,
                max_tokens=64,
            )
        except Exception:
            return True, "integrity check unavailable"

        verdict = verdict.strip()
        if verdict.upper().startswith("OK"):
            return True, verdict
        elif verdict.upper().startswith("FAIL"):
            return False, verdict
        else:
            return True, f"ambiguous verdict: {verdict}"
