"""
Referent Resolver.

Determines what 'this/it/that/the app/the project/the document' refers to
based on UI state and world profile.

Priority: selected_text > selected_node > pinned_context
          > project/document vocabulary > cartridge > unbound.
"""

import re
from typing import Optional

from src.walker.forensics.types import ReferentBinding, ReferentType


# Deictic patterns that suggest the user is pointing at something specific
_DEICTIC_RE = re.compile(
    r'\b(this|it|that|the selected|current|above|here|the function|the class'
    r'|the method|the node|the chunk|the file)\b',
    re.IGNORECASE,
)

# Patterns suggesting the user is asking about pinned context
_PINNED_RE = re.compile(
    r'\b(pinned|context items?|pinned context|the context)\b',
    re.IGNORECASE,
)

# Project/application vocabulary — binds to the whole loaded world
_PROJECT_RE = re.compile(
    r'\b(the app|the application|the project|the codebase|the repo|'
    r'the repository|whole app|whole project|entire project|'
    r'entire codebase|overall architecture|the system|'
    r'application architecture|project structure|codebase structure)\b',
    re.IGNORECASE,
)

# Document/page vocabulary — binds to document-type worlds
_DOCUMENT_RE = re.compile(
    r'\b(the document|the page|this page|that paragraph|'
    r'the chapter|the section|this section|the text|the article|'
    r'the corpus|the collection|document structure)\b',
    re.IGNORECASE,
)


def resolve_active_referent(query_text: str, ui_state: dict) -> ReferentBinding:
    """
    Resolve what 'this/it/that/the app/the document' refers to based on
    current UI state and world profile.

    Args:
        query_text: The user's query string.
        ui_state: Dict with keys:
            - selected_node_id: str or None
            - selected_node_type: str or None
            - selected_node_name: str or None
            - selected_chunk_id: str or None
            - selected_text: str or None
            - selected_file_path: str or None
            - pinned_items: list of dicts with label, chunk_id, text
            - has_cartridge: bool
            - world_profile: WorldProfile or None

    Returns:
        ReferentBinding describing the resolved referent.
    """
    has_deictic = bool(_DEICTIC_RE.search(query_text))
    has_pinned_ref = bool(_PINNED_RE.search(query_text))

    selected_text = ui_state.get("selected_text")
    selected_node_id = ui_state.get("selected_node_id")
    selected_chunk_id = ui_state.get("selected_chunk_id")
    selected_file_path = ui_state.get("selected_file_path")
    selected_node_name = ui_state.get("selected_node_name")
    pinned_items = ui_state.get("pinned_items") or []
    has_cartridge = ui_state.get("has_cartridge", False)
    world_profile = ui_state.get("world_profile")

    # Priority 1: Selected text in preview pane (most specific)
    if has_deictic and selected_text and selected_text.strip():
        return ReferentBinding(
            referent_type=ReferentType.FOCUS_TARGET,
            chunk_id=selected_chunk_id,
            node_id=selected_node_id,
            file_path=selected_file_path,
            selected_text=selected_text.strip(),
            display_label=f"Selected text in {selected_file_path or 'preview'}",
        )

    # Priority 2: Selected node in explorer tree
    if has_deictic and selected_node_id:
        label = selected_node_name or selected_node_id
        return ReferentBinding(
            referent_type=ReferentType.FOCUS_TARGET,
            node_id=selected_node_id,
            chunk_id=selected_chunk_id,
            file_path=selected_file_path,
            display_label=f"Node: {label}",
        )

    # Priority 3: Pinned context (if query explicitly references it)
    if has_pinned_ref and pinned_items:
        first = pinned_items[0]
        return ReferentBinding(
            referent_type=ReferentType.PINNED_CONTEXT,
            chunk_id=first.get("chunk_id"),
            display_label=f"Pinned: {first.get('label', 'context')}",
        )

    # Priority 4: Project/document vocabulary — world-aware resolution
    if has_cartridge and world_profile:
        world_label = getattr(world_profile, "world_label", "")
        world_kind_val = getattr(
            getattr(world_profile, "world_kind", None), "value", ""
        )

        # Project/application terms → whole loaded world
        if _PROJECT_RE.search(query_text):
            return ReferentBinding(
                referent_type=ReferentType.CARTRIDGE,
                display_label=f"Project: {world_label} ({world_kind_val})",
            )

        # Document/page terms → bind to doc-type worlds
        if _DOCUMENT_RE.search(query_text) and world_kind_val in (
            "document_corpus", "pdf_collection", "single_document",
        ):
            return ReferentBinding(
                referent_type=ReferentType.CARTRIDGE,
                display_label=f"Document: {world_label} ({world_kind_val})",
            )

    # Priority 5: Cartridge-wide (default when cartridge is loaded)
    if has_cartridge:
        label = "Cartridge (whole codebase)"
        if world_profile:
            world_label = getattr(world_profile, "world_label", "")
            if world_label:
                label = f"Cartridge: {world_label}"
        return ReferentBinding(
            referent_type=ReferentType.CARTRIDGE,
            display_label=label,
        )

    # Priority 6: No cartridge loaded
    return ReferentBinding(
        referent_type=ReferentType.UNBOUND,
        display_label="No cartridge loaded",
    )
