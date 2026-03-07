"""
Prompt Library Modal -- Browse, edit, rate, and activate prompt templates.

Layout
------
+----------------------------------------------+
|  Prompt Library                         [X]  |
+------+---------------------------------------+
| Slots|  Versions for "Synthesis Instructions" |
|      | +-----------------------------------+ |
| Sys  | | v3  My Tuned Synth  *****  [ACT]  | |
| Synth| | v2  Concise Variant ****   [ACT]  | |
| Comp | | v1  Default          ---   [ACT]  | |
| Deco | +-----------------------------------+ |
| Crit | +-----------------------------------+ |
| Iden | | >> Prompt Text Editor             | |
| Disc | |                                   | |
|  ... | |                                   | |
|      | +-----------------------------------+ |
|      | Variables: token_budget, domain_note  |
|      | Notes: _____________________________ |
|      | [New] [Duplicate] [Delete] [Save]    |
+------+---------------------------------------+
"""

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional, Callable

from src.ui.theme import COLORS, FONTS
from src.walker.prompt_library import (
    PromptLibrary, PromptEntry, SLOT_REGISTRY,
)


# Star characters for rating display
_STAR_FULL = "\u2605"   # BLACK STAR
_STAR_EMPTY = "\u2606"  # WHITE STAR


class PromptLibraryModal:
    """Modal window for managing the prompt library."""

    def __init__(
        self,
        parent: tk.Widget,
        library: Optional[PromptLibrary] = None,
        on_library_changed: Optional[Callable] = None,
    ):
        self.parent = parent
        self.library = library or PromptLibrary.load()
        self.on_library_changed = on_library_changed
        self.window: Optional[tk.Toplevel] = None

        # Internal state
        self._current_slot: str = ""
        self._current_entry: Optional[PromptEntry] = None
        self._dirty = False  # unsaved text edits

    # --- show / destroy -----------------------------------------------------

    def show(self):
        if self.window and self.window.winfo_exists():
            self.window.lift()
            return

        self.window = tk.Toplevel(self.parent)
        self.window.title("Prompt Library")
        self.window.geometry("960x700")
        self.window.resizable(True, True)
        self.window.configure(bg=COLORS["primary_bg"])
        self.window.transient(self.parent)
        self.window.grab_set()

        self._build_ui()

        # Select first slot
        slot_keys = list(SLOT_REGISTRY.keys())
        if slot_keys:
            self._slot_listbox.selection_set(0)
            self._on_slot_selected(None)

    def _destroy(self):
        if self._dirty:
            if not messagebox.askyesno(
                "Unsaved Changes",
                "You have unsaved edits. Discard them?",
                parent=self.window,
            ):
                return
        if self.window:
            self.window.destroy()
            self.window = None

    # --- build UI -----------------------------------------------------------

    def _build_ui(self):
        win = self.window

        # -- Top bar --
        top = tk.Frame(win, bg=COLORS["primary_bg"], height=36)
        top.pack(fill="x", padx=8, pady=(8, 0))
        tk.Label(
            top, text="Prompt Library",
            font=FONTS["heading"], fg=COLORS["text_primary"],
            bg=COLORS["primary_bg"],
        ).pack(side="left")

        # -- Main paned window (left slots / right editor) --
        pw = tk.PanedWindow(
            win, orient="horizontal",
            bg=COLORS["border"], sashwidth=4, sashpad=2,
        )
        pw.pack(fill="both", expand=True, padx=8, pady=8)

        # LEFT: slot list
        left = tk.Frame(pw, bg=COLORS["primary_bg"], width=200)
        pw.add(left, minsize=160)

        tk.Label(
            left, text="Prompt Slots",
            font=FONTS["ui_bold"], fg=COLORS["text_secondary"],
            bg=COLORS["primary_bg"],
        ).pack(anchor="w", padx=4, pady=(0, 4))

        self._slot_listbox = tk.Listbox(
            left,
            bg=COLORS["secondary_bg"], fg=COLORS["text_primary"],
            font=FONTS["ui"], selectbackground=COLORS["action_accent"],
            selectforeground=COLORS["selection_fg"],
            borderwidth=0, highlightthickness=1,
            highlightcolor=COLORS["action_accent"],
            highlightbackground=COLORS["border"],
            activestyle="none",
        )
        self._slot_listbox.pack(fill="both", expand=True, padx=4)
        self._slot_listbox.bind("<<ListboxSelect>>", self._on_slot_selected)

        # Populate slot list
        for key in SLOT_REGISTRY:
            defn = SLOT_REGISTRY[key]
            self._slot_listbox.insert("end", defn.display_name)

        # Slot description label
        self._slot_desc_label = tk.Label(
            left, text="", wraplength=180,
            font=FONTS["ui"], fg=COLORS["text_muted"],
            bg=COLORS["primary_bg"], justify="left", anchor="nw",
        )
        self._slot_desc_label.pack(fill="x", padx=4, pady=(4, 0))

        # RIGHT: version list + editor
        right = tk.Frame(pw, bg=COLORS["primary_bg"])
        pw.add(right, minsize=500)

        # -- Versions header --
        self._versions_header = tk.Label(
            right, text="Versions",
            font=FONTS["ui_bold"], fg=COLORS["text_secondary"],
            bg=COLORS["primary_bg"],
        )
        self._versions_header.pack(anchor="w", padx=4, pady=(0, 4))

        # -- Version list (Treeview) --
        tree_frame = tk.Frame(right, bg=COLORS["primary_bg"])
        tree_frame.pack(fill="x", padx=4)

        columns = ("version", "name", "rating", "status")
        self._version_tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings",
            height=5, selectmode="browse",
        )
        self._version_tree.heading("version", text="Ver")
        self._version_tree.heading("name", text="Name")
        self._version_tree.heading("rating", text="Rating")
        self._version_tree.heading("status", text="Status")
        self._version_tree.column("version", width=40, stretch=False)
        self._version_tree.column("name", width=260)
        self._version_tree.column("rating", width=90, stretch=False)
        self._version_tree.column("status", width=70, stretch=False)
        self._version_tree.pack(fill="x", expand=False)
        self._version_tree.bind("<<TreeviewSelect>>", self._on_version_selected)

        # -- Action toolbar --
        toolbar = tk.Frame(right, bg=COLORS["primary_bg"])
        toolbar.pack(fill="x", padx=4, pady=(4, 4))

        btn_style = {
            "bg": COLORS["widget_surface"],
            "fg": COLORS["text_primary"],
            "font": FONTS["ui"],
            "relief": "flat", "cursor": "hand2",
            "padx": 8, "pady": 2,
        }

        tk.Button(toolbar, text="New", command=self._on_new, **btn_style).pack(side="left", padx=(0, 4))
        tk.Button(toolbar, text="Duplicate", command=self._on_duplicate, **btn_style).pack(side="left", padx=(0, 4))
        tk.Button(toolbar, text="Delete", command=self._on_delete, **btn_style).pack(side="left", padx=(0, 4))
        self._activate_btn = tk.Button(toolbar, text="Activate", command=self._on_activate, **btn_style)
        self._activate_btn.pack(side="left", padx=(0, 4))
        tk.Button(toolbar, text="Reset to Default", command=self._on_reset_default, **btn_style).pack(side="left", padx=(0, 4))

        # Rating buttons
        rating_frame = tk.Frame(toolbar, bg=COLORS["primary_bg"])
        rating_frame.pack(side="right")
        tk.Label(rating_frame, text="Rate:", font=FONTS["ui"],
                 fg=COLORS["text_secondary"], bg=COLORS["primary_bg"]).pack(side="left")
        self._star_buttons = []
        for i in range(1, 6):
            btn = tk.Button(
                rating_frame, text=_STAR_EMPTY,
                font=("Segoe UI", 12), fg=COLORS["warning"],
                bg=COLORS["primary_bg"], relief="flat",
                cursor="hand2", borderwidth=0,
                command=lambda r=i: self._on_rate(r),
            )
            btn.pack(side="left", padx=0)
            self._star_buttons.append(btn)

        # -- Name entry --
        name_frame = tk.Frame(right, bg=COLORS["primary_bg"])
        name_frame.pack(fill="x", padx=4, pady=(0, 4))
        tk.Label(name_frame, text="Name:", font=FONTS["ui"],
                 fg=COLORS["text_secondary"], bg=COLORS["primary_bg"]).pack(side="left")
        self._name_var = tk.StringVar()
        self._name_entry = tk.Entry(
            name_frame, textvariable=self._name_var,
            bg=COLORS["secondary_bg"], fg=COLORS["text_primary"],
            font=FONTS["ui"], insertbackground=COLORS["text_primary"],
            borderwidth=1, relief="solid",
        )
        self._name_entry.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self._name_var.trace_add("write", lambda *_: self._mark_dirty())

        # -- Prompt text editor --
        tk.Label(
            right, text="Prompt Text:",
            font=FONTS["ui_bold"], fg=COLORS["text_secondary"],
            bg=COLORS["primary_bg"],
        ).pack(anchor="w", padx=4, pady=(0, 2))

        editor_frame = tk.Frame(right, bg=COLORS["border"], borderwidth=1, relief="solid")
        editor_frame.pack(fill="both", expand=True, padx=4)

        self._editor = tk.Text(
            editor_frame,
            bg=COLORS["secondary_bg"], fg=COLORS["text_primary"],
            font=FONTS["code"], insertbackground=COLORS["text_primary"],
            wrap="word", undo=True,
            borderwidth=0, highlightthickness=0,
        )
        editor_scroll = ttk.Scrollbar(editor_frame, command=self._editor.yview)
        self._editor.configure(yscrollcommand=editor_scroll.set)
        editor_scroll.pack(side="right", fill="y")
        self._editor.pack(fill="both", expand=True)
        self._editor.bind("<<Modified>>", self._on_editor_modified)

        # -- Variables hint --
        self._vars_label = tk.Label(
            right, text="", wraplength=700,
            font=FONTS["ui"], fg=COLORS["text_muted"],
            bg=COLORS["primary_bg"], justify="left", anchor="w",
        )
        self._vars_label.pack(fill="x", padx=4, pady=(2, 0))

        # -- Notes --
        notes_frame = tk.Frame(right, bg=COLORS["primary_bg"])
        notes_frame.pack(fill="x", padx=4, pady=(4, 0))
        tk.Label(notes_frame, text="Notes:", font=FONTS["ui"],
                 fg=COLORS["text_secondary"], bg=COLORS["primary_bg"]).pack(side="left")
        self._notes_var = tk.StringVar()
        self._notes_entry = tk.Entry(
            notes_frame, textvariable=self._notes_var,
            bg=COLORS["secondary_bg"], fg=COLORS["text_primary"],
            font=FONTS["ui"], insertbackground=COLORS["text_primary"],
            borderwidth=1, relief="solid",
        )
        self._notes_entry.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # -- Bottom buttons --
        bottom = tk.Frame(right, bg=COLORS["primary_bg"])
        bottom.pack(fill="x", padx=4, pady=(8, 4))

        tk.Button(
            bottom, text="Save Changes",
            bg=COLORS["action_accent"], fg=COLORS["text_primary"],
            font=FONTS["ui_bold"], relief="flat", cursor="hand2",
            padx=16, pady=4,
            command=self._on_save,
        ).pack(side="right", padx=(4, 0))

        tk.Button(
            bottom, text="Close",
            command=self._destroy, **btn_style,
        ).pack(side="right")

    # --- slot selection -----------------------------------------------------

    def _on_slot_selected(self, event):
        sel = self._slot_listbox.curselection()
        if not sel:
            return

        slot_keys = list(SLOT_REGISTRY.keys())
        self._current_slot = slot_keys[sel[0]]
        defn = SLOT_REGISTRY[self._current_slot]

        self._slot_desc_label.config(text=defn.description)
        self._versions_header.config(text=f"Versions for \"{defn.display_name}\"")

        # Variables hint
        if defn.variables:
            self._vars_label.config(
                text=f"Available placeholders: {{{defn.variables.replace(', ', '}}, {{')}}}",
            )
        else:
            self._vars_label.config(text="No variable placeholders for this slot.")

        self._refresh_version_tree()

    # --- version tree -------------------------------------------------------

    def _refresh_version_tree(self):
        tree = self._version_tree
        tree.delete(*tree.get_children())

        entries = self.library.for_slot(self._current_slot)
        for entry in entries:
            stars = _STAR_FULL * entry.rating + _STAR_EMPTY * (5 - entry.rating) if entry.rating else "-----"
            status = "ACTIVE" if entry.active else ""
            tree.insert(
                "", "end",
                iid=entry.prompt_id,
                values=(f"v{entry.version}", entry.name, stars, status),
            )

        # Select the first entry
        children = tree.get_children()
        if children:
            tree.selection_set(children[0])
            self._on_version_selected(None)
        else:
            self._current_entry = None
            self._clear_editor()

    def _on_version_selected(self, event):
        sel = self._version_tree.selection()
        if not sel:
            self._current_entry = None
            self._clear_editor()
            return

        entry = self.library.get_by_id(sel[0])
        if not entry:
            return

        self._current_entry = entry
        self._load_entry_into_editor(entry)

    def _load_entry_into_editor(self, entry: PromptEntry):
        """Populate the editor pane with an entry's data."""
        # Name
        self._name_var.set(entry.name)

        # Text editor
        self._editor.delete("1.0", "end")
        self._editor.insert("1.0", entry.text)
        self._editor.edit_modified(False)

        # Notes
        self._notes_var.set(entry.notes)

        # Stars
        self._update_star_display(entry.rating)

        # Activate button state
        if entry.active:
            self._activate_btn.config(state="disabled", text="Active")
        else:
            self._activate_btn.config(state="normal", text="Activate")

        self._dirty = False

    def _clear_editor(self):
        self._name_var.set("")
        self._editor.delete("1.0", "end")
        self._editor.edit_modified(False)
        self._notes_var.set("")
        self._update_star_display(0)
        self._activate_btn.config(state="disabled", text="Activate")
        self._dirty = False

    # --- star rating --------------------------------------------------------

    def _update_star_display(self, rating: int):
        for i, btn in enumerate(self._star_buttons):
            btn.config(text=_STAR_FULL if i < rating else _STAR_EMPTY)

    def _on_rate(self, rating: int):
        if not self._current_entry:
            return
        # Toggle: clicking the current rating clears it
        if self._current_entry.rating == rating:
            rating = 0
        self.library.set_rating(self._current_entry.prompt_id, rating)
        self._current_entry = self.library.get_by_id(self._current_entry.prompt_id)
        self._update_star_display(rating)
        self._refresh_version_tree()
        # Re-select the current entry
        if self._current_entry:
            try:
                self._version_tree.selection_set(self._current_entry.prompt_id)
            except Exception:
                pass
        self._save_library()

    # --- editor dirty tracking ----------------------------------------------

    def _on_editor_modified(self, event):
        if self._editor.edit_modified():
            self._dirty = True
            self._editor.edit_modified(False)

    def _mark_dirty(self):
        self._dirty = True

    # --- actions ------------------------------------------------------------

    def _on_new(self):
        if not self._current_slot:
            return
        defn = SLOT_REGISTRY.get(self._current_slot)
        default_text = defn.default_text if defn else ""
        entry = self.library.add(
            slot=self._current_slot,
            name=f"New {defn.display_name if defn else 'Prompt'}",
            text=default_text,
        )
        self._refresh_version_tree()
        self._version_tree.selection_set(entry.prompt_id)
        self._on_version_selected(None)
        self._save_library()

    def _on_duplicate(self):
        if not self._current_entry:
            return
        new_entry = self.library.duplicate(self._current_entry.prompt_id)
        if new_entry:
            self._refresh_version_tree()
            self._version_tree.selection_set(new_entry.prompt_id)
            self._on_version_selected(None)
            self._save_library()

    def _on_delete(self):
        if not self._current_entry:
            return
        # Don't let user delete the last entry for a slot
        entries = self.library.for_slot(self._current_entry.slot)
        if len(entries) <= 1:
            messagebox.showwarning(
                "Cannot Delete",
                "Each slot must have at least one prompt.",
                parent=self.window,
            )
            return
        if not messagebox.askyesno(
            "Delete Prompt",
            f"Delete \"{self._current_entry.name}\" (v{self._current_entry.version})?",
            parent=self.window,
        ):
            return
        self.library.delete(self._current_entry.prompt_id)
        self._current_entry = None
        self._refresh_version_tree()
        self._save_library()

    def _on_activate(self):
        if not self._current_entry:
            return
        self.library.activate(self._current_entry.prompt_id)
        self._refresh_version_tree()
        # Re-select
        if self._current_entry:
            try:
                self._version_tree.selection_set(self._current_entry.prompt_id)
                self._on_version_selected(None)
            except Exception:
                pass
        self._save_library()

    def _on_reset_default(self):
        """Reset the editor text to the slot's built-in default."""
        if not self._current_slot:
            return
        default_text = self.library.slot_default_text(self._current_slot)
        self._editor.delete("1.0", "end")
        self._editor.insert("1.0", default_text)
        self._dirty = True

    def _on_save(self):
        """Save the current editor state back to the entry."""
        if not self._current_entry:
            return

        new_text = self._editor.get("1.0", "end-1c")
        new_name = self._name_var.get().strip()
        new_notes = self._notes_var.get().strip()

        if not new_name:
            messagebox.showwarning("Name Required", "Prompt name cannot be empty.",
                                   parent=self.window)
            return

        self.library.update(
            self._current_entry.prompt_id,
            text=new_text,
            name=new_name,
            notes=new_notes,
        )
        self._dirty = False
        self._refresh_version_tree()
        # Re-select
        try:
            self._version_tree.selection_set(self._current_entry.prompt_id)
            self._on_version_selected(None)
        except Exception:
            pass
        self._save_library()

    # --- persistence --------------------------------------------------------

    def _save_library(self):
        """Persist the library and notify the callback."""
        self.library.save()
        if self.on_library_changed:
            self.on_library_changed(self.library)
