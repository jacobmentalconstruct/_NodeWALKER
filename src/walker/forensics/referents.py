"""
Referent Resolver.

Determines what 'this/it/that' refers to based on UI state.
Priority: selected_text > selected_node > pinned_context > cartridge > unbound.
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


def resolve_active_referent(query_text: str, ui_state: dict) -> ReferentBinding:
    """
    Resolve what 'this/it/that' refers to based on current UI state.

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

    # Priority 4: Cartridge-wide (default when cartridge is loaded)
    if has_cartridge:
        return ReferentBinding(
            referent_type=ReferentType.CARTRIDGE,
            display_label="Cartridge (whole codebase)",
        )

    # Priority 5: No cartridge loaded
    return ReferentBinding(
        referent_type=ReferentType.UNBOUND,
        display_label="No cartridge loaded",
    )
