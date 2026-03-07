"""
Evidence Packer.

Compresses collected evidence into a KV-budget-bounded package
for the final synthesis inference call.

Strategy:
1. Each facet gets a compressed summary with citations
2. Top-N heaviest verbatim chunks are included in full
3. Total tokens must fit within kv_token_budget

This is the bridge between the walker (which collects raw evidence)
and the LLM (which synthesizes the final answer).

SAFETY: The packer NEVER generates mutation/patch prompts.
Mutation is handled exclusively by src/walker/mutation_prompt.py (Phase 2).
"""

from typing import List, Dict, Optional, Tuple

from src.walker.gravity.types import (
    Facet, GravityConfig, GravitySource,
    PackedFacet, VerbatimExpansion, KVPackPlan,
)
from src.walker.gravity.engine import EvidenceGravityEngine


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return max(1, len(text) // 4)


class EvidencePacker:
    """
    Packs collected evidence into a token-budgeted synthesis payload.

    The packer produces a KVPackPlan containing:
    - Per-facet summaries with citation IDs
    - Top-N heaviest verbatim chunks (full text)
    - Token accounting to stay within budget
    """

    def __init__(
        self,
        gravity_engine: EvidenceGravityEngine,
        config: Optional[GravityConfig] = None,
        llm_agent=None,
    ):
        self.engine = gravity_engine
        self.config = config or GravityConfig()
        self.llm_agent = llm_agent

    def pack(
        self,
        facets: List[Facet],
        evidence_texts: Dict[str, str],
    ) -> KVPackPlan:
        """
        Pack all evidence into a KV-bounded plan.

        Args:
            facets: List of facets with evidence_ids populated
            evidence_texts: Map of evidence_id -> verbatim text content

        Returns:
            KVPackPlan ready for synthesis
        """
        cfg = self.config
        plan = KVPackPlan(total_budget=cfg.kv_token_budget)

        # Phase 1: Pack facet summaries (each gets a budget slice)
        facet_budget = self._allocate_facet_budgets(facets)
        for facet in facets:
            budget = facet_budget.get(facet.facet_id, cfg.facet_summary_max_tokens)
            packed = self._pack_facet(facet, evidence_texts, budget)
            plan.packed_facets.append(packed)

        # Phase 2: Select top-N heaviest verbatim expansions
        tokens_after_facets = sum(f.token_estimate for f in plan.packed_facets)
        verbatim_budget = cfg.kv_token_budget - tokens_after_facets
        expansions = self._select_verbatim_expansions(
            facets, evidence_texts, verbatim_budget
        )
        plan.verbatim_expansions = expansions

        plan.compute_remaining()
        return plan

    # =========================================================================
    # Facet Packing
    # =========================================================================

    def _allocate_facet_budgets(self, facets: List[Facet]) -> Dict[str, int]:
        """
        Allocate token budget across facets proportional to priority.

        Reserves space for verbatim expansions, then splits the rest.
        """
        cfg = self.config

        # Reserve space for verbatim expansions
        verbatim_reserve = (
            cfg.max_verbatim_expansions * cfg.verbatim_expansion_max_tokens
        )
        available = max(0, cfg.kv_token_budget - verbatim_reserve)

        if not facets:
            return {}

        # Proportional allocation by priority
        total_priority = sum(f.priority for f in facets) or 1.0
        budgets = {}

        for facet in facets:
            share = facet.priority / total_priority
            budget = int(available * share)
            budget = max(50, min(budget, cfg.facet_summary_max_tokens))
            budgets[facet.facet_id] = budget

        return budgets

    def _pack_facet(
        self,
        facet: Facet,
        evidence_texts: Dict[str, str],
        token_budget: int,
    ) -> PackedFacet:
        """
        Pack a single facet into a summary with citations.

        If LLM is available, uses it to compress. Otherwise,
        truncates and concatenates evidence texts.
        """
        # Gather evidence texts for this facet
        texts = []
        for eid in facet.evidence_ids:
            text = evidence_texts.get(eid, "")
            if text:
                texts.append((eid, text))

        if not texts:
            return PackedFacet(
                facet_id=facet.facet_id,
                question=facet.question,
                summary="[No evidence collected]",
                citations=[],
                token_estimate=10,
            )

        # Use facet.summary if already populated (by LLM during walk)
        if facet.summary:
            summary = facet.summary
        else:
            summary = self._compress_evidence(facet.question, texts, token_budget)

        citations = [eid for eid, _ in texts]

        return PackedFacet(
            facet_id=facet.facet_id,
            question=facet.question,
            summary=summary,
            citations=citations,
            token_estimate=_estimate_tokens(summary),
        )

    def _compress_evidence(
        self,
        question: str,
        texts: List[Tuple[str, str]],
        token_budget: int,
    ) -> str:
        """
        Compress evidence texts into a summary.

        Uses LLM helper if available, otherwise truncates.
        """
        # Concatenate all evidence
        combined = "\n---\n".join(
            f"[{eid}]\n{text}" for eid, text in texts
        )

        # Try LLM compression
        if self.llm_agent:
            summary = self._llm_compress(question, combined, token_budget)
            if summary and not summary.startswith("[helper"):
                return summary

        # Fallback: truncate to budget
        return self._truncate_to_budget(combined, token_budget)

    def _llm_compress(
        self,
        question: str,
        evidence: str,
        token_budget: int,
    ) -> str:
        """Use LLM helper to compress evidence into a summary."""
        world_hint = getattr(self, 'world_hint', '')
        domain_note = f" Domain: {world_hint}." if world_hint else ""

        # Use prompt library if available
        pl = getattr(self, 'prompt_library', None)
        if pl:
            template = pl.active_text("compression_instructions")
            try:
                system = template.format(
                    token_budget=token_budget, domain_note=domain_note,
                )
            except (KeyError, ValueError):
                system = template  # placeholders missing, use raw
        else:
            system = (
                f"Summarize the evidence below to answer this question in "
                f"under {token_budget} tokens. Include citation IDs in [[chunk:ID]] format. "
                f"Preserve key details and specifics from the evidence. "
                f"Be accurate and grounded in the source material.{domain_note}"
            )
        prompt = f"Question: {question}\n\nEvidence:\n{evidence}"

        try:
            return self.llm_agent.call_helper(
                system=system,
                prompt=prompt,
                max_tokens=token_budget,
            )
        except Exception:
            return ""

    @staticmethod
    def _truncate_to_budget(text: str, token_budget: int) -> str:
        """Truncate text to fit within token budget."""
        char_budget = token_budget * 4  # ~4 chars per token
        if len(text) <= char_budget:
            return text
        return text[:char_budget] + "\n[...truncated]"

    # =========================================================================
    # Verbatim Expansion Selection
    # =========================================================================

    def _select_verbatim_expansions(
        self,
        facets: List[Facet],
        evidence_texts: Dict[str, str],
        token_budget: int,
    ) -> List[VerbatimExpansion]:
        """
        Select top-N heaviest evidence items for full verbatim inclusion.

        Picks the highest-mass sources that fit within the remaining budget.
        """
        cfg = self.config

        # Gather all heavy sources across facets
        heavy_sources = self.engine.get_heavy_sources()
        heavy_sources.sort(key=lambda s: s.mass, reverse=True)

        expansions = []
        tokens_used = 0

        for source in heavy_sources:
            if len(expansions) >= cfg.max_verbatim_expansions:
                break

            text = evidence_texts.get(source.evidence_id, "")
            if not text:
                continue

            # Truncate individual expansion if needed
            max_chars = cfg.verbatim_expansion_max_tokens * 4
            if len(text) > max_chars:
                text = text[:max_chars] + "\n[...truncated]"

            token_est = _estimate_tokens(text)
            if tokens_used + token_est > token_budget:
                break

            expansions.append(VerbatimExpansion(
                evidence_id=source.evidence_id,
                text=text,
                mass=source.mass,
                token_estimate=token_est,
            ))
            tokens_used += token_est

        return expansions

    # =========================================================================
    # Rendering
    # =========================================================================

    def render_synthesis_prompt(self, plan: KVPackPlan, query: str) -> str:
        """
        Render the packed evidence into a synthesis prompt string.

        This is what gets sent to the big_brain LLM for final answer.
        """
        parts = []

        # Synthesis instructions from prompt library or default
        pl = getattr(self, 'prompt_library', None)
        synth_text = pl.active_text("synthesis_instructions") if pl else ""
        if not synth_text:
            synth_text = (
                "Answer the query below using ONLY the collected evidence. "
                "Provide a thorough, well-articulated explanation. "
                "Reference specific details from the evidence and include "
                "citation IDs ([[chunk:ID]]) to support your claims. "
                "Do not be terse -- explain your reasoning clearly."
            )
        parts.append(f"## Synthesis Instructions\n{synth_text}\n")
        parts.append(f"## Query\n{query}\n")

        # Facet summaries
        parts.append("## Evidence by Facet\n")
        for pf in plan.packed_facets:
            citations = ", ".join(f"[[chunk:{c}]]" for c in pf.citations)
            parts.append(
                f"### {pf.question}\n{pf.summary}\n"
                f"Sources: {citations}\n"
            )

        # Verbatim expansions
        if plan.verbatim_expansions:
            parts.append("## Key Evidence (Verbatim)\n")
            for ve in plan.verbatim_expansions:
                parts.append(
                    f"### [[chunk:{ve.evidence_id}]] (mass={ve.mass:.2f})\n"
                    f"```\n{ve.text}\n```\n"
                )
        else:
            # Fallback: include facet evidence inline when no verbatim
            # expansions were selected (avoids empty synthesis prompts)
            has_real_content = any(
                pf.summary and pf.summary != "[No evidence collected]"
                for pf in plan.packed_facets
            )
            if has_real_content:
                parts.append("## Evidence Content\n")
                parts.append(
                    "(No high-gravity verbatim expansions available. "
                    "Facet summaries above contain the collected evidence.)\n"
                )

        return "\n".join(parts)
