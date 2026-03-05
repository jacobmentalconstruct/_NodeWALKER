"""
Model Validator — Startup health check for model slots.

Pings Ollama and verifies each configured model can respond.
Returns structured results so the UI can gate entry to forensic mode.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from src.walker.app_settings import AppSettings, ModelSlot

# Ollama (optional import, same pattern as llm_agent.py)
try:
    import ollama
    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False


@dataclass
class SlotStatus:
    """Health status for one model slot."""
    slot_name: str          # "big_brain" or "helper"
    model_name: str
    reachable: bool = False
    installed: bool = False
    can_generate: bool = False
    error: str = ""


@dataclass
class ValidationResult:
    """Aggregate result of validating all model slots."""
    ollama_reachable: bool = False
    slots: List[SlotStatus] = field(default_factory=list)
    installed_models: List[str] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return self.ollama_reachable and all(s.can_generate for s in self.slots)

    @property
    def summary(self) -> str:
        if self.all_ok:
            names = [s.model_name for s in self.slots]
            return f"Ready. Models: {', '.join(names)}"
        problems = []
        if not self.ollama_reachable:
            problems.append("Ollama server not reachable")
        for s in self.slots:
            if not s.can_generate:
                problems.append(f"{s.slot_name}: {s.error or 'failed'}")
        return "; ".join(problems)


def _ping_ollama() -> tuple[bool, List[str]]:
    """Check if Ollama is reachable and list installed models."""
    if not OLLAMA_AVAILABLE:
        return False, []
    try:
        resp = ollama.list()
        names = [m.model for m in resp.models] if resp.models else []
        return True, names
    except Exception:
        return False, []


def _check_slot(slot_name: str, slot: ModelSlot, installed: List[str]) -> SlotStatus:
    """Validate a single model slot."""
    status = SlotStatus(slot_name=slot_name, model_name=slot.model_name)

    if not slot.is_configured():
        status.error = "No model configured"
        return status

    if not OLLAMA_AVAILABLE:
        status.error = "ollama package not installed"
        return status

    status.reachable = True

    # Check if model is installed.
    # Ollama stores models as "name:tag" — a bare name matches "name:latest".
    name = slot.model_name
    base = name.split(":")[0]
    has_tag = ":" in name
    status.installed = any(
        m == name
        or (not has_tag and m == f"{base}:latest")
        or (not has_tag and m == base)
        for m in installed
    )

    if not status.installed:
        status.error = f"Model '{slot.model_name}' not installed in Ollama"
        return status

    # Quick generation test (one token)
    try:
        resp = ollama.generate(
            model=slot.model_name,
            prompt="Reply with the single word OK.",
            options={"num_predict": 4, "num_ctx": 128},
            stream=False,
        )
        if resp.get("response", "").strip():
            status.can_generate = True
        else:
            status.error = "Model returned empty response"
    except Exception as e:
        status.error = str(e)[:120]

    return status


def validate_models(settings: AppSettings) -> ValidationResult:
    """
    Validate all model slots against Ollama.

    Call this at app startup. If result.all_ok is False,
    the UI should show the settings modal and block forensic mode.
    """
    result = ValidationResult()

    result.ollama_reachable, result.installed_models = _ping_ollama()

    if not result.ollama_reachable:
        # Still populate slot statuses so UI can show what's wrong
        for name, slot in [("big_brain", settings.big_brain), ("helper", settings.helper)]:
            s = SlotStatus(slot_name=name, model_name=slot.model_name, error="Ollama not reachable")
            result.slots.append(s)
        return result

    for name, slot in [("big_brain", settings.big_brain), ("helper", settings.helper)]:
        status = _check_slot(name, slot, result.installed_models)
        result.slots.append(status)

    return result
