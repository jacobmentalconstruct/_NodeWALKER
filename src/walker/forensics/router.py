"""
Forensic Query Router.

Classifies scope (cartridge-bound vs light chat) and intent
(explain, summarize, find, compare, mutate) using keyword rules only.
No LLM invocations at this layer.
"""

import re
from src.walker.forensics.types import ScopeLabel, IntentLabel


# =============================================================================
# Scope Classification
# =============================================================================

# Terms that strongly indicate a cartridge-bound query
_CARTRIDGE_TERMS = re.compile(
    r'\b(function|class|method|module|file|import|variable|parameter|'
    r'return|struct|interface|type|enum|const|def|node|chunk|code|'
    r'implement|declaration|call|invoke|reference|dependency|'
    r'line \d+|\.py|\.js|\.ts|\.go|\.rs|\.java|\.cpp)\b',
    re.IGNORECASE,
)

# Terms indicating social/light conversation
_SOCIAL_TERMS = re.compile(
    r'\b(hello|hi|hey|thanks|thank you|bye|goodbye|how are you|'
    r'what can you do|who are you|help me|tell me a joke)\b',
    re.IGNORECASE,
)

# Deictic references that imply cartridge context when a node/chunk is selected
_DEICTIC_RE = re.compile(
    r'\b(this|it|that|the selected|current|above|here)\b',
    re.IGNORECASE,
)


def classify_scope(query_text: str, ui_state: dict) -> ScopeLabel:
    """
    Classify whether the query is cartridge-bound or light chat.

    Rules (keyword-based, no LLM):
    - No cartridge loaded -> SOCIAL_LIGHT
    - Deictic + selected node -> NODE
    - Deictic + selected chunk -> CHUNK
    - References pinned context -> PINNED
    - Code/technical terms -> CARTRIDGE
    - Generic greeting/chat -> SOCIAL_LIGHT
    - Fallback with cartridge -> CARTRIDGE
    """
    has_cartridge = ui_state.get("has_cartridge", False)

    # No cartridge at all: everything is social
    if not has_cartridge:
        return ScopeLabel.SOCIAL_LIGHT

    has_deictic = bool(_DEICTIC_RE.search(query_text))
    selected_node_id = ui_state.get("selected_node_id")
    selected_chunk_id = ui_state.get("selected_chunk_id")
    selected_text = ui_state.get("selected_text")
    pinned_items = ui_state.get("pinned_items") or []

    # Deictic + specific selection = narrow scope
    if has_deictic:
        if selected_text and selected_text.strip():
            return ScopeLabel.CHUNK
        if selected_chunk_id:
            return ScopeLabel.CHUNK
        if selected_node_id:
            return ScopeLabel.NODE

    # Explicit pinned reference
    if re.search(r'\b(pinned|context items?)\b', query_text, re.IGNORECASE) and pinned_items:
        return ScopeLabel.PINNED

    # Strong social signal and no code terms
    if _SOCIAL_TERMS.search(query_text) and not _CARTRIDGE_TERMS.search(query_text):
        return ScopeLabel.SOCIAL_LIGHT

    # Code terms present
    if _CARTRIDGE_TERMS.search(query_text):
        return ScopeLabel.CARTRIDGE

    # Fallback: with cartridge loaded, assume cartridge-bound
    return ScopeLabel.CARTRIDGE


# =============================================================================
# Intent Classification
# =============================================================================

# Keyword -> IntentLabel mapping. First match wins (order matters).
_INTENT_RULES = [
    (IntentLabel.MUTATION, re.compile(
        r'\b(fix|refactor|change|modify|update|rename|add|remove|'
        r'replace|patch|rewrite|delete|insert|move|extract|convert)\b',
        re.IGNORECASE,
    )),
    (IntentLabel.COMPARE, re.compile(
        r'\b(compare|comparison|difference|differ|vs\.?|versus|'
        r'how do .+ and .+ differ|contrast)\b',
        re.IGNORECASE,
    )),
    (IntentLabel.SUMMARIZE, re.compile(
        r'\b(summarize|summary|summarise|tl;?dr|brief|overview|'
        r'give me an overview|high.?level|at a glance)\b',
        re.IGNORECASE,
    )),
    (IntentLabel.FIND_DEFINITION, re.compile(
        r'\b(where is|find|locate|definition of|what is|'
        r'show me|look up|search for|which file)\b',
        re.IGNORECASE,
    )),
    (IntentLabel.EXPLAIN, re.compile(
        r'\b(explain|how does|what does|describe|walk me through|'
        r'why does|tell me about|break down|elaborate)\b',
        re.IGNORECASE,
    )),
]


def classify_intent(query_text: str) -> IntentLabel:
    """
    Classify the user's intent using keyword rules (no LLM).

    First matching rule wins. Falls back to EXPLAIN for ambiguous queries.
    """
    for label, pattern in _INTENT_RULES:
        if pattern.search(query_text):
            return label

    # Default: assume the user wants an explanation
    return IntentLabel.EXPLAIN
