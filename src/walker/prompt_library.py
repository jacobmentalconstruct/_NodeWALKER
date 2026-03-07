"""
Prompt Library -- Versioned prompt management with ratings.

Stores all LLM prompt templates in ~/.nodewalker/prompt_library.json.
Each prompt slot (system_prompt, synthesis, compression, etc.) can have
multiple versioned entries with 0-5 star ratings.  One entry per slot is
marked *active* and used by the pipeline at runtime.

Usage
-----
    lib = PromptLibrary.load()
    text = lib.active_text("system_prompt")  # returns active prompt or default
    lib.save()
"""

import json
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

from src.walker.app_settings import SETTINGS_DIR


# ---------------------------------------------------------------------------
# Storage path
# ---------------------------------------------------------------------------

PROMPT_LIBRARY_FILE = SETTINGS_DIR / "prompt_library.json"


# ---------------------------------------------------------------------------
# Slot Registry -- every injectable prompt in the pipeline
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SlotDefinition:
    """Metadata for one prompt injection point."""
    key: str
    display_name: str
    description: str
    default_text: str
    variables: str = ""  # comma-sep placeholders the user can use


# fmt: off
SLOT_REGISTRY: Dict[str, SlotDefinition] = {}

def _reg(key, display_name, description, default_text, variables=""):
    SLOT_REGISTRY[key] = SlotDefinition(key, display_name, description, default_text, variables)


# -- Core LLM prompts --
_reg(
    "system_prompt",
    "System Prompt",
    "The base system prompt prepended to every LLM call.  Controls overall persona and citation rules.",
    (
        "You are a helpful assistant analyzing code and data.\n\n"
        "IMPORTANT: When referencing specific chunks or nodes, you MUST include citations in this format:\n"
        "- For chunks: [[chunk:CHUNK_ID]]\n"
        "- For nodes: [[node:NODE_ID]]\n"
        "- For files: [[file:FILE_PATH]]\n\n"
        "Example: \"The main function is defined in [[chunk:abc123]] and uses [[node:def456]].\"\n\n"
        "Always include citations to make your responses verifiable and traceable."
    ),
)

_reg(
    "synthesis_instructions",
    "Synthesis Instructions",
    "Injected at the top of the final synthesis prompt.  Guides how the LLM articulates its answer.",
    (
        "Answer the query below using ONLY the collected evidence. "
        "Provide a thorough, well-articulated explanation. "
        "Reference specific details from the evidence and include "
        "citation IDs ([[chunk:ID]]) to support your claims. "
        "Do not be terse -- explain your reasoning clearly."
    ),
)

_reg(
    "compression_instructions",
    "Evidence Compression",
    "System prompt for the helper model when compressing facet evidence.  "
    "Controls how much detail is preserved vs. discarded.",
    (
        "Summarize the evidence below to answer this question in "
        "under {token_budget} tokens. Include citation IDs in [[chunk:ID]] format. "
        "Preserve key details and specifics from the evidence. "
        "Be accurate and grounded in the source material.{domain_note}"
    ),
    variables="token_budget, domain_note",
)

_reg(
    "decomposition_system",
    "Facet Decomposition",
    "System prompt for the helper that breaks user questions into sub-questions (facets).",
    (
        "You decompose user questions about code/data into sub-questions.\n\n"
        "Given a query, return a JSON array of objects with these fields:\n"
        "- \"kind\": one of behavior, contract, failure, context, definition, comparison\n"
        "- \"question\": the sub-question (one sentence)\n"
        "- \"priority\": float 0.0-1.0 (how important this facet is)\n\n"
        "Rules:\n"
        "- Return 2-6 facets\n"
        "- Each facet must be a distinct dimension of the query\n"
        "- \"definition\" = what/where is it\n"
        "- \"behavior\" = what does it do\n"
        "- \"contract\" = inputs/outputs/types\n"
        "- \"failure\" = errors/edge cases\n"
        "- \"context\" = dependencies/related entities\n"
        "- \"comparison\" = differences between things\n\n"
        "Return ONLY valid JSON. No markdown, no explanation."
    ),
)

_reg(
    "integrity_critic",
    "Integrity Critic",
    "System prompt for the post-synthesis critic that checks answer quality.",
    (
        "You are an integrity critic. Evaluate this answer.\n"
        "Check: (1) Does it address the query? (2) Are citations present? "
        "(3) Is there scope drift?{world_note}\n"
        "Reply with ONLY: OK or FAIL: <reason>"
    ),
    variables="world_note",
)

# -- Identity / World prompts --
_reg(
    "identity_header",
    "Identity Header",
    "Template for the ## Identity section injected into every LLM call via cartridge context.",
    (
        "## Identity\n"
        "- Agent: NodeWALKER Synthesizer\n"
        "- Role: Grounded datastore examiner for code and document analysis\n"
        "- User: Operator examining the loaded world"
    ),
)

_reg(
    "discourse_rules_code",
    "Discourse Rules (Code)",
    "Mapping rules for code/project worlds.  Tells the LLM what vague terms like 'the app' mean.",
    (
        "- \"the app\", \"the project\", \"the codebase\" -> the loaded world ({world_label})\n"
        "- \"this file\" -> the currently focused file\n"
        "- \"this function/class/method\" -> the currently selected node\n"
        "- Prefer evidence from the loaded datastore over generic prior knowledge\n"
        "- Valid scope terms: project, app, codebase, file, module, class, function, method, variable, import"
    ),
    variables="world_label",
)

_reg(
    "discourse_rules_document",
    "Discourse Rules (Document)",
    "Mapping rules for document/PDF/corpus worlds.",
    (
        "- \"the document\", \"the text\", \"the corpus\" -> the loaded world ({world_label})\n"
        "- \"this page\", \"this section\" -> the currently focused node\n"
        "- \"that paragraph\" -> the most recently viewed chunk\n"
        "- Prefer evidence from the loaded datastore over generic prior knowledge\n"
        "- Valid scope terms: document, page, section, paragraph, chapter, text, corpus"
    ),
    variables="world_label",
)

_reg(
    "content_failure_warning",
    "Content Failure Warning",
    "Injected into the LLM prompt when node content cannot be loaded.  "
    "Prevents hallucination by forcing the LLM to admit the failure.",
    (
        "WARNING: The content for this node could NOT be loaded "
        "from the datastore. This is a retrieval failure, not an "
        "empty file.\n\n"
        "You MUST:\n"
        "1. Tell the user that the content for this node failed to load.\n"
        "2. Do NOT guess or hallucinate what the content might be.\n"
        "3. Suggest the user try re-ingesting the file or selecting "
        "a different node.\n"
        "4. You may still describe the node based on its metadata "
        "(name, type, path) above, but clearly state you cannot "
        "see the actual content."
    ),
)

_reg(
    "world_hint_format",
    "World Hint (compact)",
    "One-line world context injected into helper model prompts (decomposer, packer, critic).",
    "World: {language}{world_kind} '{world_label}' ({file_count} files)",
    variables="language, world_kind, world_label, file_count",
)

# -- Task instructions (right-click actions) --
_reg(
    "task_explain",
    "Task: Explain",
    "Instruction for the 'AI Explain' right-click action.",
    (
        "Explain the purpose and behavior of this code. Describe inputs, outputs, "
        "side effects, and notable patterns. Use citations."
    ),
)

_reg(
    "task_summarize",
    "Task: Summarize",
    "Instruction for the 'AI Summarize' right-click action.",
    (
        "Summarize this code in 2-4 concise bullet points. Focus on what it does, "
        "not how. Use citations."
    ),
)

_reg(
    "task_what_does",
    "Task: What Does This Do",
    "Instruction for the 'What does this do?' right-click action.",
    (
        "Explain what this code snippet does, step by step. "
        "Use citations when referencing identifiers."
    ),
)

_reg(
    "task_explain_code",
    "Task: Explain Code",
    "Instruction for the 'Explain code' preview right-click action.",
    "Explain this code's purpose, logic flow, and important details. Use citations.",
)

# fmt: on
del _reg  # clean up module namespace


# ---------------------------------------------------------------------------
# Prompt Entry
# ---------------------------------------------------------------------------

@dataclass
class PromptEntry:
    """A single versioned prompt in the library."""
    prompt_id: str
    slot: str                  # key into SLOT_REGISTRY
    name: str                  # user-facing label
    version: int               # auto-incremented per slot
    text: str                  # the actual prompt template
    rating: int = 0            # 0 = unrated, 1-5 stars
    active: bool = False       # is this the current prompt for its slot?
    created_at: str = ""       # ISO-8601
    modified_at: str = ""      # ISO-8601
    notes: str = ""            # free-form user notes

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PromptEntry":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Prompt Library
# ---------------------------------------------------------------------------

class PromptLibrary:
    """
    In-memory prompt library backed by a JSON file.

    On first load, seeds every slot with its default text (version 1, active).
    """

    def __init__(self):
        self.prompts: List[PromptEntry] = []

    # -- persistence ---------------------------------------------------------

    def save(self) -> None:
        """Write the library to disk."""
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "prompts": [p.to_dict() for p in self.prompts],
        }
        PROMPT_LIBRARY_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @classmethod
    def load(cls) -> "PromptLibrary":
        """Load from disk, seeding defaults for any missing slots."""
        lib = cls()

        if PROMPT_LIBRARY_FILE.exists():
            try:
                raw = json.loads(PROMPT_LIBRARY_FILE.read_text(encoding="utf-8"))
                for entry_dict in raw.get("prompts", []):
                    try:
                        lib.prompts.append(PromptEntry.from_dict(entry_dict))
                    except Exception:
                        pass  # skip malformed entries
            except (json.JSONDecodeError, OSError):
                pass  # start fresh

        # Seed defaults for any slot that has no entries at all
        existing_slots = {p.slot for p in lib.prompts}
        now = datetime.now(timezone.utc).isoformat()
        for key, slot_def in SLOT_REGISTRY.items():
            if key not in existing_slots:
                lib.prompts.append(PromptEntry(
                    prompt_id=str(uuid.uuid4()),
                    slot=key,
                    name=f"{slot_def.display_name} (Default)",
                    version=1,
                    text=slot_def.default_text,
                    rating=0,
                    active=True,
                    created_at=now,
                    modified_at=now,
                    notes="Built-in default prompt.",
                ))

        return lib

    # -- queries -------------------------------------------------------------

    def for_slot(self, slot: str) -> List[PromptEntry]:
        """All entries for a given slot, newest first."""
        entries = [p for p in self.prompts if p.slot == slot]
        entries.sort(key=lambda p: p.version, reverse=True)
        return entries

    def active_entry(self, slot: str) -> Optional[PromptEntry]:
        """The currently active entry for a slot, or None."""
        for p in self.prompts:
            if p.slot == slot and p.active:
                return p
        return None

    def active_text(self, slot: str) -> str:
        """
        The active prompt text for a slot.

        Falls back to the slot's built-in default if nothing is active.
        """
        entry = self.active_entry(slot)
        if entry:
            return entry.text
        defn = SLOT_REGISTRY.get(slot)
        return defn.default_text if defn else ""

    def get_by_id(self, prompt_id: str) -> Optional[PromptEntry]:
        """Find an entry by its unique ID."""
        for p in self.prompts:
            if p.prompt_id == prompt_id:
                return p
        return None

    # -- mutations -----------------------------------------------------------

    def add(self, slot: str, name: str, text: str, notes: str = "") -> PromptEntry:
        """Create a new entry for *slot* with the next version number."""
        existing = self.for_slot(slot)
        next_version = max((e.version for e in existing), default=0) + 1
        now = datetime.now(timezone.utc).isoformat()
        entry = PromptEntry(
            prompt_id=str(uuid.uuid4()),
            slot=slot,
            name=name,
            version=next_version,
            text=text,
            rating=0,
            active=False,
            created_at=now,
            modified_at=now,
            notes=notes,
        )
        self.prompts.append(entry)
        return entry

    def update(self, prompt_id: str, text: str = None, name: str = None,
               notes: str = None, rating: int = None) -> Optional[PromptEntry]:
        """Update fields on an existing entry.  Returns it or None."""
        entry = self.get_by_id(prompt_id)
        if not entry:
            return None
        if text is not None:
            entry.text = text
        if name is not None:
            entry.name = name
        if notes is not None:
            entry.notes = notes
        if rating is not None:
            entry.rating = max(0, min(5, rating))
        entry.modified_at = datetime.now(timezone.utc).isoformat()
        return entry

    def activate(self, prompt_id: str) -> None:
        """Set *prompt_id* as the active entry for its slot, deactivating others."""
        entry = self.get_by_id(prompt_id)
        if not entry:
            return
        for p in self.prompts:
            if p.slot == entry.slot:
                p.active = (p.prompt_id == prompt_id)

    def duplicate(self, prompt_id: str) -> Optional[PromptEntry]:
        """Clone an entry as a new version in the same slot."""
        source = self.get_by_id(prompt_id)
        if not source:
            return None
        return self.add(
            slot=source.slot,
            name=f"{source.name} (copy)",
            text=source.text,
            notes=source.notes,
        )

    def delete(self, prompt_id: str) -> bool:
        """Remove an entry.  Returns True if found and removed."""
        for i, p in enumerate(self.prompts):
            if p.prompt_id == prompt_id:
                was_active = p.active
                slot = p.slot
                self.prompts.pop(i)
                # If we deleted the active one, activate the newest remaining
                if was_active:
                    remaining = self.for_slot(slot)
                    if remaining:
                        remaining[0].active = True
                return True
        return False

    def set_rating(self, prompt_id: str, rating: int) -> None:
        """Set the star rating (0-5) for an entry."""
        entry = self.get_by_id(prompt_id)
        if entry:
            entry.rating = max(0, min(5, rating))
            entry.modified_at = datetime.now(timezone.utc).isoformat()

    # -- slot info -----------------------------------------------------------

    @staticmethod
    def slot_names() -> List[str]:
        """All registered slot keys in display order."""
        return list(SLOT_REGISTRY.keys())

    @staticmethod
    def slot_display_name(slot: str) -> str:
        defn = SLOT_REGISTRY.get(slot)
        return defn.display_name if defn else slot

    @staticmethod
    def slot_description(slot: str) -> str:
        defn = SLOT_REGISTRY.get(slot)
        return defn.description if defn else ""

    @staticmethod
    def slot_variables(slot: str) -> str:
        defn = SLOT_REGISTRY.get(slot)
        return defn.variables if defn else ""

    @staticmethod
    def slot_default_text(slot: str) -> str:
        defn = SLOT_REGISTRY.get(slot)
        return defn.default_text if defn else ""
