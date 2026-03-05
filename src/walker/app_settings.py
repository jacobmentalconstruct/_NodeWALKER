"""
Persistent Application Settings with Model Slots.

Stores configuration to ~/.nodewalker/settings.json.
Enforces two model slots: big_brain (primary reasoning) and helper (critics, classification).
"""

import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional


# ---------------------------------------------------------------------------
# Defaults & Recommendations
# ---------------------------------------------------------------------------

SETTINGS_DIR = Path.home() / ".nodewalker"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"

# Recommended models (shown in UI, user can override)
RECOMMENDED_BIG_BRAIN = "mistral"          # 7B — good reasoning, moderate VRAM
RECOMMENDED_HELPER = "phi3"                # 3.8B — fast classification, structured output

# KV cache defaults (tokens, not bytes)
DEFAULT_BIG_BRAIN_CTX = 4096   # safe for most GPUs
DEFAULT_HELPER_CTX = 2048      # helper only needs short context for yes/no
DEFAULT_OLLAMA_URL = "http://localhost:11434"


# ---------------------------------------------------------------------------
# Settings dataclass
# ---------------------------------------------------------------------------

@dataclass
class ModelSlot:
    """A single model slot configuration."""
    model_name: str = ""
    max_ctx_tokens: int = 4096

    def is_configured(self) -> bool:
        return bool(self.model_name.strip())


@dataclass
class AppSettings:
    """All persistent application settings."""

    # Model slots
    big_brain: ModelSlot = field(default_factory=lambda: ModelSlot(
        model_name=RECOMMENDED_BIG_BRAIN,
        max_ctx_tokens=DEFAULT_BIG_BRAIN_CTX,
    ))
    helper: ModelSlot = field(default_factory=lambda: ModelSlot(
        model_name=RECOMMENDED_HELPER,
        max_ctx_tokens=DEFAULT_HELPER_CTX,
    ))

    # Ollama connection
    ollama_url: str = DEFAULT_OLLAMA_URL
    use_ollama: bool = True

    # --- persistence ---

    def save(self) -> None:
        """Write settings to disk."""
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "big_brain": asdict(self.big_brain),
            "helper": asdict(self.helper),
            "ollama_url": self.ollama_url,
            "use_ollama": self.use_ollama,
        }
        SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls) -> "AppSettings":
        """Load settings from disk, falling back to defaults."""
        settings = cls()
        if not SETTINGS_FILE.exists():
            return settings

        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return settings

        # big_brain slot
        bb = data.get("big_brain", {})
        if isinstance(bb, dict):
            settings.big_brain.model_name = bb.get("model_name", settings.big_brain.model_name)
            settings.big_brain.max_ctx_tokens = bb.get("max_ctx_tokens", settings.big_brain.max_ctx_tokens)

        # helper slot
        hp = data.get("helper", {})
        if isinstance(hp, dict):
            settings.helper.model_name = hp.get("model_name", settings.helper.model_name)
            settings.helper.max_ctx_tokens = hp.get("max_ctx_tokens", settings.helper.max_ctx_tokens)

        # connection
        settings.ollama_url = data.get("ollama_url", settings.ollama_url)
        settings.use_ollama = data.get("use_ollama", settings.use_ollama)

        return settings

    def both_configured(self) -> bool:
        """True if both model slots have a model name set."""
        return self.big_brain.is_configured() and self.helper.is_configured()
