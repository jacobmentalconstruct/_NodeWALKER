"""
Facet Decomposer.

Splits a user query into intentful sub-questions (facets) using the
LLM helper model. Each facet represents a distinct dimension of the
query that the walker should collect evidence for independently.

Fallback: deterministic heuristic decomposition when LLM unavailable.
"""

import json
import re
import uuid
from typing import List, Optional, TYPE_CHECKING

from src.walker.gravity.types import Facet, FacetKind, GravityConfig

if TYPE_CHECKING:
    from src.walker.forensics.types import ReferentBinding, ScopeLabel


# Maps keywords in LLM output to FacetKind
_KIND_MAP = {
    "behavior": FacetKind.BEHAVIOR,
    "contract": FacetKind.CONTRACT,
    "failure": FacetKind.FAILURE,
    "context": FacetKind.CONTEXT,
    "definition": FacetKind.DEFINITION,
    "comparison": FacetKind.COMPARISON,
}

# System prompt for facet decomposition
_DECOMPOSE_SYSTEM = """You decompose user questions about code/data into sub-questions.

Given a query, return a JSON array of objects with these fields:
- "kind": one of behavior, contract, failure, context, definition, comparison
- "question": the sub-question (one sentence)
- "priority": float 0.0-1.0 (how important this facet is)

Rules:
- Return 2-6 facets
- Each facet must be a distinct dimension of the query
- "definition" = what/where is it
- "behavior" = what does it do
- "contract" = inputs/outputs/types
- "failure" = errors/edge cases
- "context" = dependencies/related entities
- "comparison" = differences between things

Return ONLY valid JSON. No markdown, no explanation."""


class FacetDecomposer:
    """
    Decomposes a query into intentful facets.

    Uses LLM helper for intelligent decomposition, falls back to
    deterministic heuristics if LLM is unavailable.
    """

    def __init__(self, llm_agent=None, config: Optional[GravityConfig] = None):
        """
        Args:
            llm_agent: LLMAgent instance (uses call_helper for decomposition)
            config: GravityConfig for max_facets and other limits
        """
        self.llm_agent = llm_agent
        self.config = config or GravityConfig()

    def decompose(
        self,
        query: str,
        referent: Optional["ReferentBinding"] = None,
        scope: Optional["ScopeLabel"] = None,
    ) -> List[Facet]:
        """
        Decompose a query into facets.

        Args:
            query: The user's query string.
            referent: Optional ReferentBinding from the forensic router.
                      When provided, seeds facet generation from the bound target.
            scope: Optional ScopeLabel from the forensic router.
                   When CARTRIDGE, starts from manifest-level questions.

        Tries LLM first, falls back to heuristic if unavailable.
        """
        if not query.strip():
            return [self._make_facet(FacetKind.DEFINITION, query or "?", 1.0)]

        # Try LLM decomposition (with referent context if available)
        if self.llm_agent:
            facets = self._decompose_llm(query, referent=referent)
            if facets:
                return facets[:self.config.max_facets]

        # Fallback: heuristic decomposition
        return self._decompose_heuristic(query, referent=referent, scope=scope)

    # =========================================================================
    # LLM Decomposition
    # =========================================================================

    def _decompose_llm(
        self,
        query: str,
        referent: Optional["ReferentBinding"] = None,
    ) -> List[Facet]:
        """Decompose using LLM helper call."""
        referent_hint = ""
        if referent and referent.display_label:
            referent_hint = (
                f"\n\nContext: The user is referring to: {referent.display_label}"
            )
            if referent.node_id:
                referent_hint += f" (node_id: {referent.node_id})"
            if referent.file_path:
                # Show just the filename, not the full system path
                fp = referent.file_path.replace("\\", "/")
                short_path = fp.rsplit("/", 1)[-1] if "/" in fp else fp
                referent_hint += f" (file: {short_path})"

        # Add world context if available
        world_hint = getattr(self, 'world_hint', '')
        if world_hint:
            referent_hint += f"\n{world_hint}"

        prompt = f"Decompose this query into sub-questions:\n\n{query}{referent_hint}"

        # Use prompt library if available, fall back to hardcoded default
        pl = getattr(self, 'prompt_library', None)
        decompose_sys = pl.active_text("decomposition_system") if pl else _DECOMPOSE_SYSTEM

        try:
            raw = self.llm_agent.call_helper(
                system=decompose_sys,
                prompt=prompt,
                max_tokens=512,
            )
        except Exception:
            return []

        return self._parse_llm_response(raw)

    def _parse_llm_response(self, raw: str) -> List[Facet]:
        """Parse JSON array from LLM response into Facet list."""
        if not raw or raw.startswith("[helper"):
            return []

        # Extract JSON array from response (may have markdown wrapping)
        json_match = re.search(r'\[.*\]', raw, re.DOTALL)
        if not json_match:
            return []

        try:
            items = json.loads(json_match.group())
        except (json.JSONDecodeError, ValueError):
            return []

        if not isinstance(items, list):
            return []

        facets = []
        for item in items:
            if not isinstance(item, dict):
                continue

            kind_str = str(item.get("kind", "custom")).lower().strip()
            kind = _KIND_MAP.get(kind_str, FacetKind.CUSTOM)
            question = str(item.get("question", "")).strip()
            priority = float(item.get("priority", 0.5))

            if question:
                facets.append(self._make_facet(kind, question, priority))

        return facets

    # =========================================================================
    # Heuristic Decomposition
    # =========================================================================

    def _decompose_heuristic(
        self,
        query: str,
        referent: Optional["ReferentBinding"] = None,
        scope: Optional["ScopeLabel"] = None,
    ) -> List[Facet]:
        """
        Deterministic fallback decomposition.

        Generates facets based on query patterns without LLM.
        When referent is provided, uses it to seed the definition facet.
        """
        q = query.lower().strip()
        facets = []

        # Determine subject from referent or query
        if referent and referent.display_label:
            subject = referent.display_label
        else:
            subject = self._extract_subject(query)

        # Always include a definition facet
        facets.append(self._make_facet(
            FacetKind.DEFINITION,
            f"What is {subject}?",
            1.0,
        ))

        # Detect intent patterns
        if any(w in q for w in ["explain", "how", "what does", "describe"]):
            facets.append(self._make_facet(
                FacetKind.BEHAVIOR,
                f"What does {self._extract_subject(query)} do?",
                0.9,
            ))

        if any(w in q for w in ["error", "fail", "bug", "wrong", "broken", "fix"]):
            facets.append(self._make_facet(
                FacetKind.FAILURE,
                f"What errors or failures occur in {self._extract_subject(query)}?",
                0.9,
            ))

        if any(w in q for w in ["input", "output", "param", "return", "type", "signature"]):
            facets.append(self._make_facet(
                FacetKind.CONTRACT,
                f"What are the inputs and outputs of {self._extract_subject(query)}?",
                0.8,
            ))

        if any(w in q for w in ["depend", "import", "call", "use", "relate"]):
            facets.append(self._make_facet(
                FacetKind.CONTEXT,
                f"What are the dependencies and related entities?",
                0.7,
            ))

        if any(w in q for w in ["compare", "differ", "versus", "vs"]):
            facets.append(self._make_facet(
                FacetKind.COMPARISON,
                f"How do the referenced items compare?",
                0.8,
            ))

        # If we only got definition, add behavior as default
        if len(facets) == 1:
            facets.append(self._make_facet(
                FacetKind.BEHAVIOR,
                f"How does {self._extract_subject(query)} work?",
                0.8,
            ))

        return facets[:self.config.max_facets]

    # =========================================================================
    # Helpers
    # =========================================================================

    @staticmethod
    def _extract_subject(query: str) -> str:
        """Extract the likely subject from a query string."""
        # Strip common question prefixes
        q = query.strip()
        for prefix in ["explain ", "describe ", "what is ", "what does ",
                        "how does ", "find ", "show me ", "where is "]:
            if q.lower().startswith(prefix):
                q = q[len(prefix):]
                break

        # Strip trailing punctuation
        q = q.rstrip("?.!")

        # Take first meaningful phrase (up to 60 chars)
        if len(q) > 60:
            q = q[:60].rsplit(" ", 1)[0]

        return q or "this"

    @staticmethod
    def _make_facet(kind: FacetKind, question: str, priority: float) -> Facet:
        """Create a Facet with a generated ID."""
        return Facet(
            facet_id=str(uuid.uuid4())[:8],
            kind=kind,
            question=question,
            priority=max(0.0, min(1.0, priority)),
        )
