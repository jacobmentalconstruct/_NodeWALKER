"""
Theme Configuration
Based on THEME_SPEC.md - Midnight Blue / Deep Slate aesthetic
"""

import tkinter as tk
from tkinter import ttk


# =============================================================================
# Color Palette (from THEME_SPEC.md)
# =============================================================================

COLORS = {
    "primary_bg": "#1e1e2f",       # Main Window background
    "secondary_bg": "#151521",     # Listboxes, Text Areas, Inset regions
    "widget_surface": "#2a2a3f",   # Buttons and Toolbars before interaction
    "action_accent": "#007ACC",    # Selection highlights, Hover states
    "status_accent": "#00FF00",    # Terminal outputs, success messages
    
    # Extended palette
    "text_primary": "#FFFFFF",
    "text_secondary": "#AAAAAA",
    "text_muted": "#666666",
    "border": "#333333",
    "error": "#FF4444",
    "warning": "#FFAA00",
    "success": "#00FF00",
    
    # Tree/list selection
    "selection_bg": "#007ACC",
    "selection_fg": "#FFFFFF",
}

# Typography
FONTS = {
    "ui": ("Segoe UI", 9),
    "ui_bold": ("Segoe UI", 9, "bold"),
    "heading": ("Segoe UI", 10, "bold"),
    "code": ("Consolas", 9),
    "code_large": ("Consolas", 10),
}


def setup_theme(root: tk.Tk) -> ttk.Style:
    """
    Configure the application theme.
    Call this once during app initialization.
    """
    # Set window background
    root.configure(bg=COLORS["primary_bg"])
    
    # Use clam as base theme
    style = ttk.Style()
    style.theme_use("clam")
    
    # ==========================================================================
    # Frame Styles
    # ==========================================================================
    
    style.configure(
        "TFrame",
        background=COLORS["primary_bg"]
    )
    
    style.configure(
        "Secondary.TFrame",
        background=COLORS["secondary_bg"]
    )
    
    style.configure(
        "Card.TFrame",
        background=COLORS["widget_surface"],
        relief="flat"
    )
    
    # ==========================================================================
    # Label Styles
    # ==========================================================================
    
    style.configure(
        "TLabel",
        background=COLORS["primary_bg"],
        foreground=COLORS["text_primary"],
        font=FONTS["ui"]
    )
    
    style.configure(
        "Heading.TLabel",
        background=COLORS["primary_bg"],
        foreground=COLORS["text_primary"],
        font=FONTS["heading"]
    )
    
    style.configure(
        "Secondary.TLabel",
        background=COLORS["secondary_bg"],
        foreground=COLORS["text_primary"],
        font=FONTS["ui"]
    )
    
    style.configure(
        "Muted.TLabel",
        background=COLORS["primary_bg"],
        foreground=COLORS["text_muted"],
        font=FONTS["ui"]
    )
    
    style.configure(
        "Success.TLabel",
        background=COLORS["primary_bg"],
        foreground=COLORS["status_accent"],
        font=FONTS["ui"]
    )
    
    style.configure(
        "Error.TLabel",
        background=COLORS["primary_bg"],
        foreground=COLORS["error"],
        font=FONTS["ui"]
    )
    
    # ==========================================================================
    # Button Styles
    # ==========================================================================
    
    style.configure(
        "TButton",
        background=COLORS["widget_surface"],
        foreground=COLORS["text_primary"],
        font=FONTS["ui"],
        borderwidth=0,
        focuscolor=COLORS["action_accent"],
        padding=(12, 6)
    )
    
    style.map(
        "TButton",
        background=[
            ("active", COLORS["action_accent"]),
            ("pressed", COLORS["action_accent"]),
        ],
        foreground=[
            ("active", COLORS["text_primary"]),
            ("pressed", COLORS["text_primary"]),
        ]
    )
    
    # Action button (primary action)
    style.configure(
        "Action.TButton",
        background=COLORS["action_accent"],
        foreground=COLORS["text_primary"],
        font=FONTS["ui_bold"],
        padding=(16, 8)
    )
    
    style.map(
        "Action.TButton",
        background=[
            ("active", "#0088DD"),
            ("pressed", "#006699"),
        ]
    )
    
    # ==========================================================================
    # Entry Styles
    # ==========================================================================
    
    style.configure(
        "TEntry",
        fieldbackground=COLORS["secondary_bg"],
        foreground=COLORS["text_primary"],
        insertcolor=COLORS["text_primary"],  # Cursor color
        borderwidth=1,
        relief="flat",
        padding=4
    )
    
    style.map(
        "TEntry",
        fieldbackground=[
            ("focus", COLORS["secondary_bg"]),
        ],
        bordercolor=[
            ("focus", COLORS["action_accent"]),
        ]
    )
    
    # ==========================================================================
    # Treeview Styles
    # ==========================================================================
    
    style.configure(
        "Treeview",
        background=COLORS["secondary_bg"],
        foreground=COLORS["text_primary"],
        fieldbackground=COLORS["secondary_bg"],
        borderwidth=0,
        font=FONTS["ui"]
    )
    
    style.configure(
        "Treeview.Heading",
        background=COLORS["widget_surface"],
        foreground=COLORS["text_primary"],
        font=FONTS["ui_bold"],
        borderwidth=0
    )
    
    style.map(
        "Treeview",
        background=[
            ("selected", COLORS["selection_bg"]),
        ],
        foreground=[
            ("selected", COLORS["selection_fg"]),
        ]
    )
    
    style.map(
        "Treeview.Heading",
        background=[
            ("active", COLORS["action_accent"]),
        ]
    )
    
    # ==========================================================================
    # Scrollbar Styles
    # ==========================================================================
    
    style.configure(
        "Vertical.TScrollbar",
        background=COLORS["widget_surface"],
        troughcolor=COLORS["secondary_bg"],
        borderwidth=0,
        arrowsize=12
    )
    
    style.configure(
        "Horizontal.TScrollbar",
        background=COLORS["widget_surface"],
        troughcolor=COLORS["secondary_bg"],
        borderwidth=0,
        arrowsize=12
    )
    
    style.map(
        "Vertical.TScrollbar",
        background=[
            ("active", COLORS["action_accent"]),
            ("pressed", COLORS["action_accent"]),
        ]
    )
    
    # ==========================================================================
    # Notebook (Tabs) Styles
    # ==========================================================================
    
    style.configure(
        "TNotebook",
        background=COLORS["primary_bg"],
        borderwidth=0
    )
    
    style.configure(
        "TNotebook.Tab",
        background=COLORS["widget_surface"],
        foreground=COLORS["text_primary"],
        padding=(12, 6),
        font=FONTS["ui"]
    )
    
    style.map(
        "TNotebook.Tab",
        background=[
            ("selected", COLORS["primary_bg"]),
            ("active", COLORS["action_accent"]),
        ],
        foreground=[
            ("selected", COLORS["text_primary"]),
        ]
    )
    
    # ==========================================================================
    # Progressbar Styles
    # ==========================================================================
    
    style.configure(
        "TProgressbar",
        background=COLORS["action_accent"],
        troughcolor=COLORS["secondary_bg"],
        borderwidth=0,
        thickness=8
    )
    
    # ==========================================================================
    # Separator
    # ==========================================================================
    
    style.configure(
        "TSeparator",
        background=COLORS["border"]
    )
    
    # ==========================================================================
    # Checkbutton
    # ==========================================================================
    
    style.configure(
        "TCheckbutton",
        background=COLORS["primary_bg"],
        foreground=COLORS["text_primary"],
        font=FONTS["ui"]
    )
    
    style.map(
        "TCheckbutton",
        background=[
            ("active", COLORS["primary_bg"]),
        ],
        foreground=[
            ("active", COLORS["action_accent"]),
        ]
    )
    
    # ==========================================================================
    # Combobox
    # ==========================================================================
    
    style.configure(
        "TCombobox",
        fieldbackground=COLORS["secondary_bg"],
        background=COLORS["widget_surface"],
        foreground=COLORS["text_primary"],
        arrowcolor=COLORS["text_primary"],
        borderwidth=0
    )
    
    style.map(
        "TCombobox",
        fieldbackground=[
            ("focus", COLORS["secondary_bg"]),
        ],
        selectbackground=[
            ("focus", COLORS["action_accent"]),
        ]
    )
    
    # Configure the dropdown list
    root.option_add("*TCombobox*Listbox.background", COLORS["secondary_bg"])
    root.option_add("*TCombobox*Listbox.foreground", COLORS["text_primary"])
    root.option_add("*TCombobox*Listbox.selectBackground", COLORS["action_accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", COLORS["text_primary"])
    
    # ==========================================================================
    # LabelFrame
    # ==========================================================================
    
    style.configure(
        "TLabelframe",
        background=COLORS["primary_bg"],
        foreground=COLORS["text_primary"],
        borderwidth=1,
        relief="groove"
    )
    
    style.configure(
        "TLabelframe.Label",
        background=COLORS["primary_bg"],
        foreground=COLORS["text_secondary"],
        font=FONTS["ui_bold"]
    )
    
    return style


def configure_text_widget(text_widget: tk.Text, readonly: bool = False):
    """Configure a Text widget to match the theme"""
    text_widget.configure(
        bg=COLORS["secondary_bg"],
        fg=COLORS["text_primary"],
        insertbackground=COLORS["text_primary"],  # Cursor
        selectbackground=COLORS["action_accent"],
        selectforeground=COLORS["text_primary"],
        font=FONTS["code"],
        borderwidth=0,
        highlightthickness=1,
        highlightbackground=COLORS["border"],
        highlightcolor=COLORS["action_accent"],
        padx=8,
        pady=8
    )
    
    if readonly:
        text_widget.configure(state="disabled")


def configure_listbox(listbox: tk.Listbox):
    """Configure a Listbox widget to match the theme"""
    listbox.configure(
        bg=COLORS["secondary_bg"],
        fg=COLORS["text_primary"],
        selectbackground=COLORS["action_accent"],
        selectforeground=COLORS["text_primary"],
        font=FONTS["ui"],
        borderwidth=0,
        highlightthickness=1,
        highlightbackground=COLORS["border"],
        highlightcolor=COLORS["action_accent"]
    )


def configure_canvas(canvas: tk.Canvas):
    """Configure a Canvas widget to match the theme"""
    canvas.configure(
        bg=COLORS["primary_bg"],
        highlightthickness=0,
        borderwidth=0
    )
