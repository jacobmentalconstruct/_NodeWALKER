"""
Patch Approval Modal.

Tkinter modal dialog that presents a PatchProposal for user review.
Shows:
- File path header
- Side-by-side search / replace blocks
- Unified diff
- Evidence list
- Reject / Apply buttons (Apply disabled if verification fails)
"""

import tkinter as tk
from tkinter import ttk
from typing import Optional, Callable, List

from src.ui.theme import COLORS, FONTS
from src.walker.types import PatchProposal, PatchVerificationResult


class PatchApprovalModal:
    """
    Modal dialog for reviewing and approving a proposed code patch.

    Usage:
        modal = PatchApprovalModal(parent, proposal, verification, evidence_texts)
        modal.show()
        # modal.result is True if user clicked Apply, False if Reject/closed.
    """

    def __init__(
        self,
        parent: tk.Widget,
        proposal: PatchProposal,
        verification: PatchVerificationResult,
        diff_text: str = "",
        evidence_texts: Optional[List[str]] = None,
        on_approve: Optional[Callable] = None,
        on_reject: Optional[Callable] = None,
    ):
        self.parent = parent
        self.proposal = proposal
        self.verification = verification
        self.diff_text = diff_text
        self.evidence_texts = evidence_texts or []
        self.on_approve = on_approve
        self.on_reject = on_reject
        self.result = False
        self._window: Optional[tk.Toplevel] = None

    def show(self) -> None:
        """Display the modal."""
        self._window = tw = tk.Toplevel(self.parent)
        tw.title("Patch Approval")
        tw.geometry("800x600")
        tw.configure(bg=COLORS.get("primary_bg", "#1a1a2e"))
        tw.transient(self.parent)
        tw.grab_set()

        # -- Header --
        header = tk.Label(
            tw,
            text=f"Proposed Change: {self.proposal.target_file_path}",
            bg=COLORS.get("primary_bg", "#1a1a2e"),
            fg=COLORS.get("text_primary", "#FFFFFF"),
            font=FONTS.get("heading", ("Segoe UI", 12, "bold")),
            anchor="w",
            padx=10,
            pady=8,
        )
        header.pack(fill="x")

        if self.verification.found:
            line_info = tk.Label(
                tw,
                text=f"Match at line {self.verification.line_number}",
                bg=COLORS.get("primary_bg", "#1a1a2e"),
                fg="#44FF44",
                font=FONTS.get("code", ("Consolas", 9)),
                anchor="w",
                padx=10,
            )
            line_info.pack(fill="x")
        else:
            err_info = tk.Label(
                tw,
                text=f"VERIFICATION FAILED: {self.verification.error}",
                bg=COLORS.get("primary_bg", "#1a1a2e"),
                fg="#FF4444",
                font=FONTS.get("code", ("Consolas", 9)),
                anchor="w",
                padx=10,
            )
            err_info.pack(fill="x")

        # -- Notebook with tabs --
        nb = ttk.Notebook(tw)
        nb.pack(fill="both", expand=True, padx=10, pady=5)

        # Tab 1: Search / Replace side-by-side
        sr_frame = tk.Frame(nb, bg=COLORS.get("secondary_bg", "#242444"))
        nb.add(sr_frame, text="Search / Replace")
        self._build_search_replace_tab(sr_frame)

        # Tab 2: Unified diff
        diff_frame = tk.Frame(nb, bg=COLORS.get("secondary_bg", "#242444"))
        nb.add(diff_frame, text="Diff")
        self._build_diff_tab(diff_frame)

        # Tab 3: Evidence
        if self.evidence_texts:
            ev_frame = tk.Frame(nb, bg=COLORS.get("secondary_bg", "#242444"))
            nb.add(ev_frame, text="Evidence")
            self._build_evidence_tab(ev_frame)

        # -- Justification --
        if self.proposal.justification:
            just_label = tk.Label(
                tw,
                text=f"Justification: {self.proposal.justification}",
                bg=COLORS.get("primary_bg", "#1a1a2e"),
                fg=COLORS.get("text_muted", "#8888AA"),
                font=FONTS.get("ui", ("Segoe UI", 10)),
                anchor="w",
                padx=10,
                wraplength=760,
            )
            just_label.pack(fill="x", pady=(5, 0))

        # -- Buttons --
        btn_frame = tk.Frame(tw, bg=COLORS.get("primary_bg", "#1a1a2e"))
        btn_frame.pack(fill="x", padx=10, pady=10)

        reject_btn = ttk.Button(
            btn_frame, text="Reject", command=self._on_reject, width=12
        )
        reject_btn.pack(side="right", padx=(5, 0))

        apply_btn = ttk.Button(
            btn_frame, text="Apply", command=self._on_approve, width=12
        )
        apply_btn.pack(side="right")

        # Disable Apply if verification failed
        if not self.verification.found:
            apply_btn.config(state="disabled")

        tw.protocol("WM_DELETE_WINDOW", self._on_reject)

    def _build_search_replace_tab(self, parent: tk.Frame) -> None:
        """Build side-by-side search/replace view."""
        paned = tk.PanedWindow(
            parent, orient="horizontal",
            bg=COLORS.get("secondary_bg", "#242444"),
            sashwidth=4,
        )
        paned.pack(fill="both", expand=True, padx=5, pady=5)

        # Search (left)
        search_frame = tk.LabelFrame(
            paned, text="Search (original)", padx=5, pady=5,
            bg=COLORS.get("secondary_bg", "#242444"),
            fg=COLORS.get("text_primary", "#FFFFFF"),
        )
        paned.add(search_frame)
        search_text = tk.Text(
            search_frame, wrap="none",
            bg=COLORS.get("secondary_bg", "#242444"),
            fg="#FF8888",
            font=FONTS.get("code", ("Consolas", 9)),
        )
        search_text.pack(fill="both", expand=True)
        search_text.insert("1.0", self.proposal.search_block)
        search_text.config(state="disabled")

        # Replace (right)
        replace_frame = tk.LabelFrame(
            paned, text="Replace (proposed)", padx=5, pady=5,
            bg=COLORS.get("secondary_bg", "#242444"),
            fg=COLORS.get("text_primary", "#FFFFFF"),
        )
        paned.add(replace_frame)
        replace_text = tk.Text(
            replace_frame, wrap="none",
            bg=COLORS.get("secondary_bg", "#242444"),
            fg="#88FF88",
            font=FONTS.get("code", ("Consolas", 9)),
        )
        replace_text.pack(fill="both", expand=True)
        replace_text.insert("1.0", self.proposal.replace_block)
        replace_text.config(state="disabled")

    def _build_diff_tab(self, parent: tk.Frame) -> None:
        """Build unified diff view."""
        diff_widget = tk.Text(
            parent, wrap="none",
            bg=COLORS.get("secondary_bg", "#242444"),
            fg=COLORS.get("text_primary", "#FFFFFF"),
            font=FONTS.get("code", ("Consolas", 9)),
        )
        diff_widget.pack(fill="both", expand=True, padx=5, pady=5)

        diff_widget.tag_configure("add", foreground="#88FF88")
        diff_widget.tag_configure("remove", foreground="#FF8888")
        diff_widget.tag_configure("header", foreground="#8888FF")

        for line in self.diff_text.split("\n"):
            if line.startswith("+"):
                diff_widget.insert("end", line + "\n", "add")
            elif line.startswith("-"):
                diff_widget.insert("end", line + "\n", "remove")
            elif line.startswith("@@"):
                diff_widget.insert("end", line + "\n", "header")
            else:
                diff_widget.insert("end", line + "\n")

        diff_widget.config(state="disabled")

    def _build_evidence_tab(self, parent: tk.Frame) -> None:
        """Build evidence list view."""
        ev_widget = tk.Text(
            parent, wrap="word",
            bg=COLORS.get("secondary_bg", "#242444"),
            fg=COLORS.get("text_primary", "#FFFFFF"),
            font=FONTS.get("code", ("Consolas", 9)),
        )
        ev_widget.pack(fill="both", expand=True, padx=5, pady=5)

        for i, text in enumerate(self.evidence_texts, 1):
            ev_widget.insert("end", f"--- Evidence {i} ---\n{text}\n\n")

        ev_widget.config(state="disabled")

    def _on_approve(self) -> None:
        self.result = True
        if self._window:
            self._window.destroy()
        if self.on_approve:
            self.on_approve()

    def _on_reject(self) -> None:
        self.result = False
        if self._window:
            self._window.destroy()
        if self.on_reject:
            self.on_reject()
