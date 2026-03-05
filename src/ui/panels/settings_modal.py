"""
Settings Modal — Dual Model Slot Configuration.

Provides UI for:
- Configuring big_brain (primary reasoning) and helper (critics/classification) model slots
- Setting KV cache limits per slot
- Validating models against Ollama
- Persisting all settings to disk
"""

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional, Callable, List
import threading

from src.ui.theme import COLORS, FONTS
from src.walker.app_settings import (
    AppSettings, ModelSlot,
    RECOMMENDED_BIG_BRAIN, RECOMMENDED_HELPER,
    DEFAULT_BIG_BRAIN_CTX, DEFAULT_HELPER_CTX,
)

# Try to import ollama (graceful fallback if not installed)
try:
    import ollama
    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False


class SettingsModal:
    """Settings modal with dual model slots and persistent storage."""

    def __init__(
        self,
        parent: tk.Widget,
        settings: Optional[AppSettings] = None,
        on_settings_changed: Optional[Callable] = None,
    ):
        """
        Args:
            parent: Parent window
            settings: AppSettings instance (loaded from disk)
            on_settings_changed: Callback(settings: AppSettings) when user saves
        """
        self.parent = parent
        self.settings = settings or AppSettings.load()
        self.on_settings_changed = on_settings_changed
        self.window: Optional[tk.Toplevel] = None
        self.installed_models: List[str] = []

    # --- legacy compatibility -----------------------------------------------
    # CircuitHighlightingWindow still calls on_model_changed(name, tokens).
    # Bridge it so existing code doesn't break.
    @property
    def current_model(self) -> str:
        return self.settings.big_brain.model_name

    @property
    def max_tokens(self) -> int:
        return self.settings.big_brain.max_ctx_tokens

    def get_config(self) -> dict:
        return {
            "model": self.settings.big_brain.model_name,
            "max_tokens": self.settings.big_brain.max_ctx_tokens,
            "helper_model": self.settings.helper.model_name,
            "helper_max_tokens": self.settings.helper.max_ctx_tokens,
            "use_ollama": self.settings.use_ollama,
            "ollama_url": self.settings.ollama_url,
        }

    # --- show / build -------------------------------------------------------

    def show(self):
        """Open the settings modal."""
        if self.window and self.window.winfo_exists():
            self.window.lift()
            return

        self.window = tk.Toplevel(self.parent)
        self.window.title("Model Settings")
        self.window.geometry("580x820")
        self.window.resizable(False, False)

        self._fetch_models_background()
        self._build_ui()

    def _build_ui(self):
        main_frame = ttk.Frame(self.window)
        main_frame.pack(fill="both", expand=True, padx=0, pady=0)

        content = ttk.Frame(main_frame)
        content.pack(fill="x", expand=False, padx=10, pady=10)

        ttk.Label(content, text="Model Configuration", font=FONTS["heading"]).pack(
            anchor="w", pady=(0, 15)
        )

        # Big Brain slot
        self._build_slot_section(
            content,
            title="Big Brain (Primary Reasoning)",
            description=f"Main model for answers and synthesis. Recommended: {RECOMMENDED_BIG_BRAIN}",
            slot=self.settings.big_brain,
            default_ctx=DEFAULT_BIG_BRAIN_CTX,
            var_prefix="bb",
        )

        # Helper slot
        self._build_slot_section(
            content,
            title="Helper (Critics / Classification)",
            description=f"Small, fast model for yes/no critic checks. Recommended: {RECOMMENDED_HELPER}",
            slot=self.settings.helper,
            default_ctx=DEFAULT_HELPER_CTX,
            var_prefix="hp",
        )

        # Ollama section
        self._build_ollama_section(content)

        # Status
        self._build_status_section(content)

        # Buttons
        self._build_buttons(main_frame)

    # --- slot section -------------------------------------------------------

    def _build_slot_section(
        self,
        parent: tk.Widget,
        title: str,
        description: str,
        slot: ModelSlot,
        default_ctx: int,
        var_prefix: str,
    ):
        frame = ttk.LabelFrame(parent, text=title, padding=10)
        frame.pack(fill="x", pady=(0, 12))

        ttk.Label(frame, text=description, font=FONTS["ui"], wraplength=520).pack(
            anchor="w", pady=(0, 6)
        )

        # Model dropdown + refresh
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(0, 6))

        ttk.Label(row, text="Model:").pack(side="left", padx=(0, 6))

        model_var = tk.StringVar(value=slot.model_name)
        combo = ttk.Combobox(row, textvariable=model_var, state="readonly", width=36)
        combo.pack(side="left", fill="x", expand=True, padx=(0, 6))

        refresh_btn = ttk.Button(
            row, text="Refresh", command=self._on_refresh_models, width=8
        )
        refresh_btn.pack(side="right")

        # Context tokens
        ctx_row = ttk.Frame(frame)
        ctx_row.pack(fill="x", pady=(0, 4))

        ttk.Label(ctx_row, text="Max context tokens:").pack(side="left", padx=(0, 6))

        ctx_var = tk.StringVar(value=str(slot.max_ctx_tokens))
        ctx_entry = ttk.Entry(ctx_row, textvariable=ctx_var, width=8, justify="right")
        ctx_entry.pack(side="left", padx=(0, 8))

        for tokens in [1024, 2048, 4096]:
            ttk.Button(
                ctx_row,
                text=f"{tokens:,}",
                command=lambda t=tokens, v=ctx_var: v.set(str(t)),
                width=6,
            ).pack(side="left", padx=2)

        # Install button
        install_btn = ttk.Button(
            frame,
            text="Install Selected Model",
            command=lambda mv=model_var: self._on_install_model(mv.get()),
        )
        install_btn.pack(anchor="w", pady=(4, 0))

        # Store refs for save
        setattr(self, f"_{var_prefix}_model_var", model_var)
        setattr(self, f"_{var_prefix}_ctx_var", ctx_var)
        setattr(self, f"_{var_prefix}_combo", combo)

    # --- ollama section -----------------------------------------------------

    def _build_ollama_section(self, parent: tk.Widget):
        frame = ttk.LabelFrame(parent, text="Ollama Connection", padding=10)
        frame.pack(fill="x", pady=(0, 12))

        self.use_ollama_var = tk.BooleanVar(value=self.settings.use_ollama)
        ttk.Checkbutton(frame, text="Use Local Ollama Server", variable=self.use_ollama_var).pack(
            anchor="w", pady=(0, 8)
        )

        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(0, 6))
        ttk.Label(row, text="Endpoint:").pack(side="left", padx=(0, 6))

        self.ollama_url_var = tk.StringVar(value=self.settings.ollama_url)
        ttk.Entry(row, textvariable=self.ollama_url_var, width=36).pack(
            side="left", fill="x", expand=True, padx=(0, 6)
        )

        ttk.Button(row, text="Test", command=self._on_test_ollama, width=6).pack(side="right")

    # --- status section -----------------------------------------------------

    def _build_status_section(self, parent: tk.Widget):
        frame = ttk.LabelFrame(parent, text="Status", padding=10)
        frame.pack(fill="x", pady=(0, 12))

        self.status_text = tk.Text(
            frame, height=3, width=50, wrap="word",
            bg=COLORS["secondary_bg"], fg="#00FF00", font=FONTS["code"],
        )
        self.status_text.pack(fill="both", expand=True)
        self._update_status_display()

    def _update_status_display(self):
        if not hasattr(self, "status_text"):
            return
        self.status_text.config(state="normal")
        self.status_text.delete("1.0", "end")
        s = self.settings
        text = (
            f"Big Brain: {s.big_brain.model_name or '(none)'} "
            f"[ctx: {s.big_brain.max_ctx_tokens:,}]\n"
            f"Helper:    {s.helper.model_name or '(none)'} "
            f"[ctx: {s.helper.max_ctx_tokens:,}]\n"
            f"Ollama:    {'Enabled' if s.use_ollama else 'Disabled'} @ {s.ollama_url}"
        )
        self.status_text.insert("1.0", text)
        self.status_text.config(state="disabled")

    # --- buttons ------------------------------------------------------------

    def _build_buttons(self, parent: tk.Widget):
        bf = ttk.Frame(parent)
        bf.pack(fill="x", padx=10, pady=(10, 10))

        ttk.Button(bf, text="Apply", command=self._on_apply).pack(side="left", padx=(0, 5))
        ttk.Button(bf, text="Save & Close", command=self._on_save).pack(side="left", padx=(0, 5))
        ttk.Button(bf, text="Cancel", command=self._on_cancel).pack(side="left")

    # --- actions ------------------------------------------------------------

    def _on_apply(self):
        """Validate inputs and apply to self.settings (no disk write yet)."""
        # Big brain
        bb_model = self._bb_model_var.get()
        try:
            bb_ctx = int(self._bb_ctx_var.get())
        except ValueError:
            messagebox.showerror("Error", "Big Brain context tokens must be a number.")
            return

        # Helper
        hp_model = self._hp_model_var.get()
        try:
            hp_ctx = int(self._hp_ctx_var.get())
        except ValueError:
            messagebox.showerror("Error", "Helper context tokens must be a number.")
            return

        if not bb_model or not hp_model:
            messagebox.showwarning("Warning", "Both model slots must be configured.")
            return

        self.settings.big_brain = ModelSlot(model_name=bb_model, max_ctx_tokens=bb_ctx)
        self.settings.helper = ModelSlot(model_name=hp_model, max_ctx_tokens=hp_ctx)
        self.settings.ollama_url = self.ollama_url_var.get()
        self.settings.use_ollama = self.use_ollama_var.get()

        self._update_status_display()

        if self.on_settings_changed:
            self.on_settings_changed(self.settings)

        messagebox.showinfo(
            "Applied",
            f"Big Brain: {bb_model} [ctx: {bb_ctx:,}]\n"
            f"Helper: {hp_model} [ctx: {hp_ctx:,}]",
        )

    def _on_save(self):
        """Apply, persist to disk, and close."""
        self._on_apply()
        self.settings.save()
        if self.window:
            self.window.destroy()

    def _on_cancel(self):
        if self.window:
            self.window.destroy()

    # --- model fetching / install -------------------------------------------

    def _fetch_models_background(self):
        def fetch():
            try:
                if not OLLAMA_AVAILABLE:
                    return
                resp = ollama.list()
                if resp.models:
                    self.installed_models = [m.model for m in resp.models]
                if self.window and self.window.winfo_exists():
                    self.window.after(0, self._update_combos)
            except Exception:
                pass

        threading.Thread(target=fetch, daemon=True).start()

    def _update_combos(self):
        models = self.installed_models or []
        for prefix in ("_bb", "_hp"):
            combo = getattr(self, f"{prefix}_combo", None)
            if combo:
                combo["values"] = models

    def _on_refresh_models(self):
        self._fetch_models_background()

    def _on_install_model(self, model_name: str):
        if not model_name:
            messagebox.showwarning("Warning", "Select a model first.")
            return
        if not OLLAMA_AVAILABLE:
            messagebox.showerror(
                "Error",
                "Ollama Python library not installed.\n\n"
                "1. Install Ollama from https://ollama.ai\n"
                "2. pip install ollama\n"
                "3. ollama serve",
            )
            return

        progress = tk.Toplevel(self.window)
        progress.title("Installing Model")
        progress.geometry("500x120")
        progress.resizable(False, False)
        progress.transient(self.window)

        ttk.Label(progress, text=f"Pulling {model_name}...", font=FONTS["heading"]).pack(pady=10)
        bar = ttk.Progressbar(progress, mode="indeterminate")
        bar.pack(fill="x", padx=20, pady=5)
        bar.start()
        status = ttk.Label(progress, text="Connecting...", font=FONTS["code"])
        status.pack(pady=5)

        def download():
            try:
                ollama.pull(model_name)
                if progress.winfo_exists():
                    self.window.after(0, progress.destroy)
                    self.window.after(0, lambda: messagebox.showinfo(
                        "Success", f"'{model_name}' installed."
                    ))
                    self.window.after(0, self._on_refresh_models)
            except Exception as e:
                msg = str(e)[:200]
                if progress.winfo_exists():
                    self.window.after(0, progress.destroy)
                    self.window.after(0, lambda: messagebox.showerror(
                        "Failed", f"Could not pull '{model_name}':\n{msg}"
                    ))

        threading.Thread(target=download, daemon=True).start()

    def _on_test_ollama(self):
        url = self.ollama_url_var.get()

        def test():
            try:
                if OLLAMA_AVAILABLE:
                    models = ollama.list()
                    names = [m.model for m in models.models] if models.models else []
                    models_str = "\n".join(f"  - {m}" for m in names[:10]) or "  (none)"
                    self.window.after(0, lambda: messagebox.showinfo(
                        "Connected", f"Ollama OK at {url}\n\nInstalled:\n{models_str}"
                    ))
                else:
                    import urllib.request, json as _json
                    resp = urllib.request.urlopen(f"{url}/api/tags", timeout=5)
                    data = _json.loads(resp.read().decode())
                    names = [m["name"] for m in data.get("models", [])]
                    models_str = "\n".join(f"  - {m}" for m in names[:10]) or "  (none)"
                    self.window.after(0, lambda: messagebox.showinfo(
                        "Connected", f"Ollama OK at {url}\n\nInstalled:\n{models_str}"
                    ))
            except Exception as e:
                self.window.after(0, lambda: messagebox.showerror(
                    "Failed", f"Cannot reach Ollama at {url}\n\n{e}"
                ))

        threading.Thread(target=test, daemon=True).start()
