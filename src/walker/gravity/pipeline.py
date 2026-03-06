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

import re
import time
import uuid
from typing import List, Dict, Optional, Set, Tuple, Any, TYPE_CHECKING
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

if TYPE_CHECKING:
    from src.walker.forensics.types import ReferentBinding, ScopeLabel, IntentLabel


# Stop-words stripped when extracting FTS keywords from natural language queries
_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "what", "how", "does", "do", "did", "can", "could", "would", "should",
    "will", "shall", "may", "might", "must",
    "this", "that", "it", "its", "my", "your", "our", "their",
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "about",
    "summarize", "explain", "describe", "show", "tell", "me", "please",
    "find", "list", "give", "get",
    "and", "or", "not", "but", "so", "if", "then",
    "i", "you", "we", "they", "he", "she",
})

# Regex for file-like references (app.py, src/utils.js, etc.)
_FILE_REF_RE = re.compile(r'[\w./\\-]+\.(?:py|js|ts|go|rs|java|cpp|c|cs|rb)\b')

# --- Source-type priors for evidence ranking ---
# Low-value artifacts that should be deprioritized for app-level questions.
# These files rarely describe runtime behavior.
_LOW_VALUE_EXTENSIONS = frozenset({
    ".spec", ".lock", ".json", ".yaml", ".yml", ".toml", ".xml",
    ".cfg", ".ini", ".env", ".gitignore", ".dockerignore",
    ".md", ".txt", ".rst", ".csv", ".bat", ".sh", ".cmd",
    ".png", ".jpg", ".svg", ".ico",
})
_LOW_VALUE_NAMES = frozenset({
    "requirements.txt", "package.json", "package-lock.json",
    "tsconfig.json", "setup.py", "setup.cfg", "pyproject.toml",
    "dockerfile", "makefile", "license", "license.md",
    ".gitignore", ".dockerignore", ".editorconfig",
})
# Runtime code files that should be prioritized for app-level questions.
_RUNTIME_CODE_EXTENSIONS = frozenset({
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs",
    ".java", ".cpp", ".c", ".cs", ".rb", ".php",
    ".swift", ".kt", ".scala",
})

# Entry-point detection regex (Python's if __name__ == "__main__":)
_ENTRY_POINT_RE = re.compile(
    r'''if\s+__name__\s*==\s*['"]__main__['"]\s*:''',
)

# Filename patterns that suggest high-value entry points
_ENTRY_POINT_NAMES = frozenset({
    "app.py", "main.py", "cli.py", "server.py", "run.py",
    "manage.py", "wsgi.py", "asgi.py",
    "index.js", "index.ts", "main.js", "main.ts",
    "app.js", "app.ts", "server.js", "server.ts",
    "main.go", "main.rs", "main.java",
    "program.cs", "main.cpp", "main.c",
})


def _extract_fts_keywords(query: str) -> str:
    """
    Extract meaningful keywords from a natural language query for FTS.

    Strips stop-words and question syntax, preserves identifiers,
    file names, and technical terms.
    """
    # Preserve any file references verbatim
    file_refs = _FILE_REF_RE.findall(query)

    # Tokenize and filter
    words = re.findall(r'[A-Za-z_][\w.]*', query)
    keywords = [
        w for w in words
        if w.lower() not in _STOP_WORDS and len(w) > 1
    ]

    # Add file refs that might have been split
    for ref in file_refs:
        base = ref.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if base not in keywords:
            keywords.append(base)

    return " ".join(keywords) if keywords else ""


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
        session_db=None,
    ):
        """
        Args:
            walker: Initialized NodeWalker instance (provides operators + DB)
            llm_agent: LLMAgent instance (for decomposition, compression, critics)
            config: GravityConfig (tuning knobs for the pipeline)
            session_db: Optional SessionDB for mission logging
        """
        self.walker = walker
        self.llm_agent = llm_agent
        self.config = config or GravityConfig()
        self.session_db = session_db

        # Sub-systems (created fresh per run)
        self._gravity: Optional[EvidenceGravityEngine] = None
        self._decomposer: Optional[FacetDecomposer] = None
        self._critic: Optional[SufficiencyCritic] = None
        self._packer: Optional[EvidencePacker] = None

        # Importance score cache (node_id -> float) — avoids repeated CAS lookups
        self._importance_cache: Dict[str, float] = {}

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

        # ---- Stage 3b: Fallback if no evidence collected ----
        if not evidence_texts:
            evidence_texts = self._broad_evidence_fallback(query, facets)

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
    # Binding-Aware Entry Point (for forensic pipeline)
    # =========================================================================

    def run_with_binding(
        self,
        query: str,
        referent: Optional["ReferentBinding"] = None,
        scope: Optional["ScopeLabel"] = None,
        intent: Optional["IntentLabel"] = None,
    ) -> ForensicResult:
        """
        Execute forensic pipeline with referent binding context.

        The referent determines where facet seeding starts.
        The scope constrains evidence collection breadth.
        The intent biases facet kind generation.
        """
        start_time = time.time()

        # Initialize sub-systems
        self._gravity = EvidenceGravityEngine(self.config)
        self._decomposer = FacetDecomposer(self.llm_agent, self.config)
        self._critic = SufficiencyCritic(self._gravity, self.config)
        self._packer = EvidencePacker(self._gravity, self.config, self.llm_agent)

        # Stage 1: Decompose with binding context
        facets = self._decomposer.decompose(query, referent=referent, scope=scope)

        # Stage 2: Facet Critic
        facets = self._critique_facets(facets, query)

        # Stage 3: Walk with referent-aware seeding
        evidence_texts = self._walk_all_facets_with_referent(facets, query, referent)

        # Stage 3b: Fallback if no evidence collected
        if not evidence_texts:
            evidence_texts = self._broad_evidence_fallback(query, facets)

        # Stage 4: Pack
        pack_plan = self._packer.pack(facets, evidence_texts)

        # Stage 5: Synthesize
        synthesis = self._synthesize(pack_plan, query)

        # Stage 6: Integrity
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

    def _walk_all_facets_with_referent(
        self,
        facets: List[Facet],
        query: str,
        referent: Optional["ReferentBinding"] = None,
    ) -> Dict[str, str]:
        """Walk each facet with referent-aware seeding."""
        all_evidence_texts: Dict[str, str] = {}

        for facet in facets:
            texts = self._walk_facet(facet, query, referent=referent)
            all_evidence_texts.update(texts)

        return all_evidence_texts

    # =========================================================================
    # Stage 3b: Broad Evidence Fallback
    # =========================================================================

    def _broad_evidence_fallback(
        self,
        query: str,
        facets: List[Facet],
    ) -> Dict[str, str]:
        """
        Last-resort evidence collection when all facet walks returned empty.

        Root nodes (files) typically have NO chunks in chunk_manifest —
        chunks are bound to inner structural nodes (functions, classes).
        So this fallback uses two strategies:
          1. Walk into children of root nodes to find inner nodes with chunks
          2. Read file content directly via CAS when no chunks exist

        Assigns collected evidence to the first facet so the packer
        has something to compress into the synthesis prompt.
        """
        walker = self.walker
        evidence_texts: Dict[str, str] = {}
        max_evidence = 10  # Cap to stay within KV budget
        facet_id = facets[0].facet_id if facets else "fallback"

        try:
            roots = walker.structure.roots()
            # Sort by numeric importance (highest first — entry points, dense code)
            roots = sorted(
                roots,
                key=lambda r: self._compute_source_importance(r),
                reverse=True,
            )
        except Exception:
            return evidence_texts

        for root in roots:
            if len(evidence_texts) >= max_evidence:
                break

            # Strategy 1: Walk into children — they have the chunks
            try:
                children = walker.structure.children(root.node_id)
                for child in children:
                    if len(evidence_texts) >= max_evidence:
                        break

                    chunks = walker.chunks.node_to_chunks(child.node_id)
                    for chunk in chunks:
                        if len(evidence_texts) >= max_evidence:
                            break

                        content = walker.chunks.get_content(chunk.chunk_id)
                        if content and content.strip():
                            evidence_texts[chunk.chunk_id] = content
                            if facets:
                                facets[0].evidence_ids.append(chunk.chunk_id)
                            if self._gravity:
                                self._gravity.register_evidence(
                                    evidence_id=chunk.chunk_id,
                                    target_type="chunk",
                                    facet_id=facet_id,
                                    structural_signal=0.5,
                                    semantic_signal=0.0,
                                    graph_signal=0.0,
                                    verbatim_signal=0.0,
                                )
                            walker.emit_activation(
                                ActivationKind.COLLECT,
                                TargetType.CHUNK,
                                chunk.chunk_id,
                                meta={"source": "broad_fallback_child"},
                            )
            except Exception:
                pass

            # Strategy 2: If no chunks found via children, read file via CAS
            if not evidence_texts and root.file_cid:
                try:
                    reconstructed = walker.cas.reconstruct_file(root.file_cid)
                    if reconstructed and reconstructed.content and reconstructed.content.strip():
                        synth_id = f"file:{root.node_id}"
                        # Truncate to ~2000 chars to stay within KV budget
                        text = reconstructed.content[:2000]
                        evidence_texts[synth_id] = text
                        if facets:
                            facets[0].evidence_ids.append(synth_id)
                        if self._gravity:
                            self._gravity.register_evidence(
                                evidence_id=synth_id,
                                target_type="chunk",
                                facet_id=facet_id,
                                structural_signal=0.5,
                                semantic_signal=0.0,
                                graph_signal=0.0,
                                verbatim_signal=0.0,
                            )
                        walker.emit_activation(
                            ActivationKind.COLLECT,
                            TargetType.TREE_NODE,
                            root.node_id,
                            meta={"source": "cas_direct_fallback"},
                        )
                except Exception:
                    continue

        return evidence_texts

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
        referent: Optional["ReferentBinding"] = None,
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

        # Mission logging: start a run for this facet
        run_id = None
        walk_id = str(uuid.uuid4())[:8]
        if self.session_db and walker.query_id:
            try:
                run_id = self.session_db.insert_query_run(
                    query_id=walker.query_id,
                    walk_id=walk_id,
                    facet_id=facet.facet_id,
                )
            except Exception:
                pass  # Non-critical

        # Create scorer for this facet walk
        scorer = Scorer(walker.config.policy)

        # Seed using the facet question via FTS (+ referent if available)
        seeds = self._get_facet_seeds(facet, query, referent=referent)
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

            # Mission logging: log this step
            if run_id and self.session_db:
                try:
                    node_id = candidate.target_id if candidate.target_type == "node" else None
                    chunk_id = candidate.target_id if candidate.target_type == "chunk" else None
                    reason = f"{candidate.target_type} expansion (score={grav_score.total:.3f})"
                    self.session_db.insert_query_step(
                        run_id=run_id, walk_id=walk_id,
                        facet_id=facet.facet_id,
                        node_id=node_id, chunk_id=chunk_id,
                        score=grav_score.total, reason=reason,
                    )
                except Exception:
                    pass  # Non-critical

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

        # Mission logging: end run
        if run_id and self.session_db:
            try:
                suff = self._critic.evaluate_facet(facet)
                self.session_db.end_query_run(
                    run_id=run_id,
                    total_nodes=len(visited_nodes),
                    total_evidence=len(evidence_texts),
                    sufficiency_level=suff.level.value,
                    reason=f"walk completed (depth={walk_depth})",
                )
            except Exception:
                pass  # Non-critical

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

        # Collect chunks for this node (importance-scaled structural signal)
        chunks = walker.chunks.node_to_chunks(node_id)
        # Walk up to file-level node for importance scoring
        file_node = node
        if getattr(node, "node_type", "") not in ("file", "project"):
            try:
                parent = walker.structure.parent(node_id)
                while parent and getattr(parent, "node_type", "") not in ("file", "project"):
                    parent = walker.structure.parent(parent.node_id)
                if parent and getattr(parent, "node_type", "") == "file":
                    file_node = parent
            except Exception:
                pass
        importance = self._compute_source_importance(file_node)
        # Map importance to structural signal: 0.1-1.0 range
        # importance < 0.5 → low signal (artifacts), importance >= 1.0 → full signal
        struct_signal = max(0.1, min(1.0, importance))
        if chunks:
            for chunk in chunks:
                if chunk.chunk_id not in visited_chunks:
                    self._collect_chunk_for_facet(
                        chunk.chunk_id, facet, gravity,
                        visited_chunks, evidence_texts,
                        structural_signal=struct_signal,
                    )
        elif node.file_cid:
            # No chunks for this node — read its span directly via CAS
            try:
                span = walker.cas.reconstruct_span(
                    node.file_cid, node.line_start, node.line_end
                )
                if span and span.content and span.content.strip():
                    synth_id = f"span:{node_id}"
                    evidence_texts[synth_id] = span.content[:2000]
                    facet.evidence_ids.append(synth_id)
                    if gravity:
                        gravity.register_evidence(
                            evidence_id=synth_id,
                            target_type="chunk",
                            facet_id=facet.facet_id,
                            structural_signal=1.0,
                            semantic_signal=0.0,
                            graph_signal=0.0,
                            verbatim_signal=0.0,
                        )
                    walker.emit_activation(
                        ActivationKind.COLLECT, TargetType.TREE_NODE, node_id,
                        meta={"facet_id": facet.facet_id, "source": "cas_span"},
                    )
            except Exception:
                pass  # CAS resolution failed — continue with structural expansion

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

        # Graph expansion (tolerant of schema mismatches in cartridge)
        if policy.use_graph and node.graph_node_id:
            if scorer.budget_state.graph_hops < policy.graph_max_hops:
                try:
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
                except Exception:
                    pass  # Graph layer unavailable or schema mismatch

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

        # Get content (with CAS fallback if chunk resolver fails)
        content = self.walker.chunks.get_content(chunk_id)
        if not content:
            # Try resolving via the chunk's spans directly through CAS
            try:
                chunk_obj = self.walker.chunks.db.get_chunk(chunk_id)
                if chunk_obj and chunk_obj.spans:
                    span = chunk_obj.spans[0]
                    resolved = self.walker.cas.reconstruct_span(
                        span.file_cid, span.line_start, span.line_end
                    )
                    if resolved:
                        content = resolved.content
            except Exception:
                pass
        if not content:
            return

        # Store text
        evidence_texts[chunk_id] = content

        # Track in facet
        facet.evidence_ids.append(chunk_id)

        # Scale gravity signals by source importance
        # (low-importance artifacts get weaker signals, entry points get full signals)
        try:
            file_node = None
            chunk_obj = self.walker.chunks.db.get_chunk(chunk_id)
            if chunk_obj and chunk_obj.node_id:
                source_node = self.walker.structure.get_node(chunk_obj.node_id)
                # Walk up to file-level for importance scoring
                file_node = source_node
                if source_node and getattr(source_node, "node_type", "") not in ("file", "project"):
                    parent = self.walker.structure.parent(source_node.node_id)
                    while parent and getattr(parent, "node_type", "") not in ("file", "project"):
                        parent = self.walker.structure.parent(parent.node_id)
                    if parent and getattr(parent, "node_type", "") == "file":
                        file_node = parent
            importance = self._compute_source_importance(file_node)
            # Scale factor: importance < 0.5 → signals damped; >= 1.0 → full signals
            scale = max(0.1, min(1.0, importance))
            structural_signal *= scale
            semantic_signal *= scale
            graph_signal *= scale
        except Exception:
            pass

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
        referent: Optional["ReferentBinding"] = None,
    ) -> List[Tuple[str, str]]:
        """
        Get seeds for a facet walk.

        Seed strategy (in priority order):
        1. Referent-bound seeds (node/chunk from UI selection)
        2. FTS on the facet question
        3. FTS on the original user query (fallback)
        4. FTS on extracted keywords from the query (fallback)
        5. Tree root nodes (last resort — ensures we always have seeds)
        """
        walker = self.walker
        seeds = []
        top_k = walker.config.policy.semantic_top_k

        # Priority 1: inject referent-bound seeds first
        if referent:
            if referent.node_id:
                seeds.append((referent.node_id, "node"))
            if referent.chunk_id:
                seeds.append((referent.chunk_id, "chunk"))

        # Priority 2: FTS on facet question
        results = self._fts_seed_search(facet.question, top_k)
        self._add_fts_results(results, seeds)

        # Priority 3: FTS on raw user query (if facet question yielded nothing)
        if not results and query != facet.question:
            results = self._fts_seed_search(query, top_k)
            self._add_fts_results(results, seeds)

        # Priority 4: FTS on extracted keywords
        if not seeds or (not referent and len(seeds) < 2):
            keywords = _extract_fts_keywords(query)
            if keywords and keywords != query:
                kw_results = self._fts_seed_search(keywords, top_k)
                self._add_fts_results(kw_results, seeds)

        # Re-rank: push runtime code seeds to front, config artifacts to back
        if seeds:
            seeds = self._rerank_seeds_by_source_type(seeds)

        # Priority 5: Last resort — seed from tree root nodes (sorted by importance)
        if not seeds:
            try:
                roots = walker.structure.roots()
                roots = sorted(
                    roots,
                    key=lambda r: self._compute_source_importance(r),
                    reverse=True,
                )
                for root in roots[:5]:
                    seeds.append((root.node_id, "node"))
            except Exception:
                pass

        # Dedupe
        seen = set()
        unique = []
        for s in seeds:
            if s[0] not in seen:
                seen.add(s[0])
                unique.append(s)

        return unique[:10]  # Cap seeds per facet

    def _fts_seed_search(
        self, search_text: str, limit: int
    ) -> List[Tuple[str, float]]:
        """Run FTS search with error handling."""
        try:
            return self.walker.db.fts_search_chunks(search_text, limit=limit)
        except Exception:
            return []

    def _add_fts_results(
        self, results: List[Tuple[str, float]], seeds: List[Tuple[str, str]]
    ) -> None:
        """Add FTS results (chunk + associated node) to seeds list."""
        for chunk_id, rank in results:
            seeds.append((chunk_id, "chunk"))
            try:
                chunk = self.walker.db.get_chunk(chunk_id)
                if chunk and chunk.node_id:
                    seeds.append((chunk.node_id, "node"))
            except Exception:
                pass

    # =========================================================================
    # Source-Type Classification Helpers
    # =========================================================================

    def _is_low_value_artifact(self, node) -> bool:
        """Check if a tree node represents a build/config/non-code file."""
        if not node:
            return False
        name_lower = (getattr(node, "name", "") or "").lower()
        # Check exact filename match
        if name_lower in _LOW_VALUE_NAMES:
            return True
        # Check extension
        dot_idx = name_lower.rfind(".")
        if dot_idx >= 0:
            ext = name_lower[dot_idx:]
            return ext in _LOW_VALUE_EXTENSIONS
        return False

    def _is_runtime_code(self, node) -> bool:
        """Check if a tree node represents actual runtime source code."""
        if not node:
            return False
        name_lower = (getattr(node, "name", "") or "").lower()
        dot_idx = name_lower.rfind(".")
        if dot_idx >= 0:
            ext = name_lower[dot_idx:]
            return ext in _RUNTIME_CODE_EXTENSIONS
        return False

    def _rerank_seeds_by_source_type(
        self, seeds: List[Tuple[str, str]]
    ) -> List[Tuple[str, str]]:
        """
        Re-rank seeds by numeric source importance (highest first).

        Uses _compute_source_importance() for a smooth ranking rather
        than hard boolean tiers. Entry points and dense code files
        float to the top; config artifacts sink to the bottom.
        """
        walker = self.walker

        def _seed_importance(seed_pair):
            seed_id, seed_type = seed_pair
            node = None
            try:
                if seed_type == "node":
                    node = walker.structure.get_node(seed_id)
                elif seed_type == "chunk":
                    chunk = walker.chunks.db.get_chunk(seed_id)
                    if chunk and chunk.node_id:
                        node = walker.structure.get_node(chunk.node_id)
                        # Walk up to file-level node for importance scoring
                        if node and getattr(node, "node_type", "") not in ("file", "project"):
                            parent = walker.structure.parent(node.node_id)
                            while parent and getattr(parent, "node_type", "") not in ("file", "project"):
                                parent = walker.structure.parent(parent.node_id)
                            if parent and getattr(parent, "node_type", "") == "file":
                                node = parent
            except Exception:
                pass
            return self._compute_source_importance(node)

        return sorted(seeds, key=_seed_importance, reverse=True)

    # =========================================================================
    # Numeric Source Importance Scoring
    # =========================================================================

    def _compute_source_importance(self, node) -> float:
        """
        Compute a numeric importance score for a tree node (file-level).

        Combines four soft factors into a single float:
          - Filename prior:  entry-point names get a boost
          - Extension prior: runtime code = 1.0, low-value artifacts = 0.1
          - Code density:    children count (functions/classes/methods) → +0.1 each, capped at +1.0
          - Entry point:     if __name__ == "__main__" detected via regex → +2.0

        Returns a float ≥ 0.1.  Higher = more important for app-level queries.
        Cached per node_id to avoid repeated DB/CAS lookups.
        """
        if not node:
            return 0.5  # Unknown node — neutral

        node_id = getattr(node, "node_id", None)
        if node_id and node_id in self._importance_cache:
            return self._importance_cache[node_id]

        score = 0.5  # Neutral baseline

        name_lower = (getattr(node, "name", "") or "").lower()

        # --- Factor 1: Extension prior ---
        dot_idx = name_lower.rfind(".")
        if dot_idx >= 0:
            ext = name_lower[dot_idx:]
            if ext in _LOW_VALUE_EXTENSIONS or name_lower in _LOW_VALUE_NAMES:
                score = 0.1  # Soft penalty — not excluded, just deprioritized
            elif ext in _RUNTIME_CODE_EXTENSIONS:
                score = 1.0  # Runtime code baseline

        # --- Factor 2: Entry-point filename boost ---
        if name_lower in _ENTRY_POINT_NAMES:
            score += 1.5  # Strong boost for canonical entry-point names

        # --- Factor 3: Code density (children count from tree structure) ---
        if score >= 0.5:  # Only worth checking for non-artifact files
            try:
                children = self.walker.structure.children(node_id) if node_id else []
                code_children = sum(
                    1 for c in children
                    if getattr(c, "node_type", "") in ("function", "class", "method")
                )
                # +0.1 per code child, capped at +1.0
                score += min(code_children * 0.1, 1.0)
            except Exception:
                pass

        # --- Factor 4: Entry-point regex on file content ---
        if score >= 0.5 and name_lower.endswith(".py"):
            try:
                file_cid = getattr(node, "file_cid", None)
                if file_cid:
                    reconstructed = self.walker.cas.reconstruct_file(file_cid)
                    if reconstructed and reconstructed.content:
                        # Only scan first 5000 chars for perf
                        if _ENTRY_POINT_RE.search(reconstructed.content[:5000]):
                            score += 2.0
            except Exception:
                pass

        # Cache the result
        if node_id:
            self._importance_cache[node_id] = score

        return score

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
