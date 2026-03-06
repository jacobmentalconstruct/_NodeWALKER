"""
Forensic Query Pipeline -- Orchestrator.

Ties together: router -> referent resolver -> gravity pipeline -> answer packing.
Calls INTO gravity; does not rewrite it.

Usage:
    result = run_forensic_query(
        query_text="Explain this function",
        walker=walker,
        llm_agent=llm_agent,
        ui_state=build_ui_state(),
        session_db=session_db,
    )
"""

import json
import re
import time
from typing import Optional, Dict, Any, List

from src.walker.forensics.types import (
    ScopeLabel, IntentLabel, ReferentBinding, ReferentType, ManifoldResult,
    SufficiencySummary, FacetResult,
)
from src.walker.forensics.referents import resolve_active_referent
from src.walker.forensics.router import classify_scope, classify_intent
from src.walker.gravity.pipeline import ForensicPipeline, ForensicResult
from src.walker.gravity.types import GravityConfig
from src.walker.mutation_prompt import build_patch_prompt
from src.walker.patcher import verify_exact_match
from src.walker.types import PatchProposal
from src.ui.event_bus import get_event_bus, PATCH_PROPOSED


# Regex for file-like references in query text
_FILE_REF_RE = re.compile(
    r'([\w./\\-]+\.(?:py|js|ts|go|rs|java|cpp|c|cs|rb|json|yaml|yml|toml|md))\b'
)


def _refine_referent_with_file_lookup(
    referent: ReferentBinding,
    query_text: str,
    walker,
) -> ReferentBinding:
    """
    If the query explicitly mentions a file (e.g. 'summarize app.py')
    and the referent is currently CARTRIDGE-wide, narrow it to that file's node.

    This ensures file-specific queries get scoped evidence instead of
    whole-codebase sweeps.
    """
    # Only refine CARTRIDGE-level referents (already-focused ones are fine)
    if referent.referent_type != ReferentType.CARTRIDGE:
        return referent

    match = _FILE_REF_RE.search(query_text)
    if not match:
        return referent

    filename = match.group(1)
    # Strip path separators to get just the basename for matching
    basename = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]

    # Search tree roots for a node whose name matches
    try:
        roots = walker.structure.roots()
        for root in roots:
            if root.name == basename or root.path.endswith(basename):
                return ReferentBinding(
                    referent_type=ReferentType.FOCUS_TARGET,
                    node_id=root.node_id,
                    chunk_id=root.chunk_id,
                    file_path=root.path or basename,
                    display_label=f"File: {basename}",
                )
    except Exception:
        pass

    # Didn't find a matching node — keep original referent
    return referent


def run_forensic_query(
    query_text: str,
    walker,
    llm_agent,
    ui_state: dict,
    session_db=None,
    config: Optional[GravityConfig] = None,
) -> ManifoldResult:
    """
    Full forensic query pipeline entry point.

    Steps:
    1. Classify scope (cartridge-bound vs light chat)
    2. Classify intent (explain, summarize, find, compare, mutation)
    3. Resolve referent (what 'this/it' points to)
    4. If SOCIAL_LIGHT: fall back to LLM-only chat
    5. Dispatch to gravity pipeline with binding
    6. Build ManifoldResult with synthesis + evidence + sufficiency
    7. Record in session DB

    Args:
        query_text: User's query string.
        walker: Initialized NodeWalker instance.
        llm_agent: LLMAgent instance.
        ui_state: Dict describing current UI selection state.
        session_db: Optional SessionDB for logging.
        config: Optional GravityConfig overrides.

    Returns:
        ManifoldResult with answer, evidence IDs, drift warnings.
    """
    start = time.time()

    # Step 1-2: Router classification
    scope = classify_scope(query_text, ui_state)
    intent = classify_intent(query_text)

    # Step 3: Referent resolution
    referent = resolve_active_referent(query_text, ui_state)

    # Step 3b: Refine referent if query mentions a specific file
    referent = _refine_referent_with_file_lookup(referent, query_text, walker)

    # Step 4: Social light fallback (no gravity needed)
    if scope == ScopeLabel.SOCIAL_LIGHT:
        return _social_light_fallback(
            query_text, llm_agent, scope, intent, referent, start,
        )

    # Step 5: Dispatch to gravity pipeline with binding
    gravity_pipeline = ForensicPipeline(
        walker=walker,
        llm_agent=llm_agent,
        config=config or GravityConfig(),
        session_db=session_db,
    )

    gravity_result = gravity_pipeline.run_with_binding(
        query=query_text,
        referent=referent,
        scope=scope,
        intent=intent,
    )

    # Step 6: Pack into ManifoldResult
    evidence_ids = []
    for facet in gravity_result.facets:
        evidence_ids.extend(facet.evidence_ids)

    drift_warnings = []
    if not gravity_result.integrity_ok:
        drift_warnings.append(
            f"Integrity concern: {gravity_result.integrity_notes}"
        )

    facet_results = [
        FacetResult(
            facet_id=f.facet_id,
            question=f.question,
            evidence_count=f.evidence_count,
            heavy_evidence_count=len(f.evidence_ids),
            sufficient=f.sufficient,
            summary=f.summary,
        )
        for f in gravity_result.facets
    ]

    sufficiency = SufficiencySummary(
        report=gravity_result.sufficiency,
        tokens_used=(
            gravity_result.pack_plan.tokens_used
            if gravity_result.pack_plan else 0
        ),
        search_party_needed=False,
        facet_summaries=facet_results,
    )

    elapsed_ms = int((time.time() - start) * 1000)

    result = ManifoldResult(
        query=query_text,
        scope=scope,
        intent=intent,
        referent=referent,
        synthesis=gravity_result.synthesis,
        evidence_ids=evidence_ids,
        drift_warnings=drift_warnings,
        sufficiency=sufficiency,
        gravity_result=gravity_result,
        elapsed_ms=elapsed_ms,
        facet_results=facet_results,
    )

    # Step 7: If MUTATION intent with sufficient evidence, generate patch
    if intent == IntentLabel.MUTATION and evidence_ids and llm_agent:
        _attempt_mutation(result, gravity_result, llm_agent, walker)

    # Step 8: Record in session DB
    if session_db:
        _record_result(session_db, result)

    return result


def _social_light_fallback(
    query_text: str,
    llm_agent,
    scope: ScopeLabel,
    intent: IntentLabel,
    referent: ReferentBinding,
    start: float,
) -> ManifoldResult:
    """Handle non-cartridge queries with direct LLM chat."""
    synthesis = ""
    if llm_agent:
        try:
            response, _citations = llm_agent.process_prompt(
                prompt=query_text,
                session_id="social",
                include_tier2_3=False,
            )
            synthesis = response
        except Exception as e:
            synthesis = f"[LLM error: {e}]"
    else:
        synthesis = "[No LLM agent available for chat.]"

    elapsed_ms = int((time.time() - start) * 1000)

    return ManifoldResult(
        query=query_text,
        scope=scope,
        intent=intent,
        referent=referent,
        synthesis=synthesis,
        drift_warnings=["Light conversation -- no cartridge evidence used."],
        elapsed_ms=elapsed_ms,
    )


def _attempt_mutation(
    result: ManifoldResult,
    gravity_result: ForensicResult,
    llm_agent,
    walker,
) -> None:
    """
    After sufficient evidence is gathered for a MUTATION intent,
    call the LLM with the mutation prompt, parse the JSON PatchProposal,
    verify exact match, and emit PATCH_PROPOSED event.
    """
    try:
        # Build evidence bundle from gravity result
        evidence_bundle: List[Dict[str, Any]] = []
        cas = getattr(walker, "cas", None)
        for facet in gravity_result.facets:
            for eid in facet.evidence_ids[:5]:  # Cap at 5 per facet
                entry: Dict[str, Any] = {"chunk_id": eid, "score": 1.0}
                if cas:
                    try:
                        content = cas.resolve_chunk_by_id(eid)
                        if content:
                            entry["content"] = content
                    except Exception:
                        pass
                evidence_bundle.append(entry)

        if not evidence_bundle:
            return

        # Build and send mutation prompt
        prompt = build_patch_prompt(evidence_bundle, result.intent.value)
        raw_response, _ = llm_agent.process_prompt(
            prompt=prompt,
            session_id="mutation",
            include_tier2_3=False,
        )

        # Parse JSON from response (strip markdown fences if present)
        json_text = raw_response.strip()
        if json_text.startswith("```"):
            # Remove ```json ... ``` wrapper
            lines = json_text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            json_text = "\n".join(lines)

        patch_data = json.loads(json_text)

        proposal = PatchProposal(
            target_file_path=patch_data.get("target_file_path", ""),
            search_block=patch_data.get("search_block", ""),
            replace_block=patch_data.get("replace_block", ""),
            evidence_ids=[e.get("chunk_id", "") for e in evidence_bundle],
            justification=patch_data.get("justification", ""),
        )

        # Verify exact match
        verification = verify_exact_match(proposal)

        # Emit PATCH_PROPOSED event for the UI to handle
        bus = get_event_bus()
        bus.emit(PATCH_PROPOSED, {
            "proposal": proposal,
            "verification": verification,
            "evidence_bundle": evidence_bundle,
        })

    except (json.JSONDecodeError, KeyError, TypeError):
        # LLM didn't produce valid JSON — silently skip mutation
        pass
    except Exception:
        pass  # Non-critical: mutation is best-effort


def _record_result(session_db, result: ManifoldResult) -> None:
    """Record the forensic result in session DB as a summary."""
    try:
        # Find or use existing session
        session_id = "forensic"
        if hasattr(result, "gravity_result") and result.gravity_result:
            gr = result.gravity_result
            if hasattr(gr, "query"):
                summary_text = (
                    f"[{result.scope.value}/{result.intent.value}] "
                    f"Q: {result.query[:100]} | "
                    f"Evidence: {len(result.evidence_ids)} | "
                    f"Elapsed: {result.elapsed_ms}ms"
                )
                session_db.insert_summary(session_id, tier=1, content=summary_text)
    except Exception:
        pass  # Non-critical
