"""
Node Walker Main Window
Tkinter-based UI following THEME_SPEC.md
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import Optional
import threading
import queue
from datetime import datetime

from src.ui.theme import (
    setup_theme, COLORS, FONTS,
    configure_text_widget, configure_listbox
)
from src.walker import (
    CartridgeDB, NodeWalker, WalkerConfig,
    ManifestReader, ReadinessLevel,
    PolicySelector, TraversalPolicy, TraversalMode
)
from src.walker.session_db import SessionDB
from src.walker.activation_store import ActivationStore
from src.walker.llm_agent import LLMAgent
from src.walker.app_settings import AppSettings
from src.walker.model_validator import validate_models
from src.ui.circuit_window import CircuitHighlightingWindow


class NodeWalkerApp:
    """Main application window"""
    
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Node Walker - Systems Thinker Edition")
        self.root.geometry("1200x800")
        self.root.minsize(900, 600)
        
        # Setup theme
        self.style = setup_theme(root)
        
        # State
        self.db: Optional[CartridgeDB] = None
        self.walker: Optional[NodeWalker] = None
        self.cartridge_path: Optional[Path] = None
        self.session_db: Optional[SessionDB] = None
        self.circuit_window: Optional[CircuitHighlightingWindow] = None

        # Load persistent settings
        self.app_settings = AppSettings.load()

        # Message queue for thread communication
        self.msg_queue = queue.Queue()

        # Build UI
        self._build_ui()

        # Start message processor
        self._process_messages()

        # Validate models in background (non-blocking)
        self._validate_models_background()
    
    def _build_ui(self):
        """Build the main UI layout"""
        # Main container
        self.main_frame = ttk.Frame(self.root, style="TFrame")
        self.main_frame.pack(fill="both", expand=True, padx=0, pady=0)
        
        # Top bar (cartridge selection)
        self._build_top_bar()
        
        # Main content area (3 columns)
        self._build_content_area()
        
        # Bottom bar (status + actions)
        self._build_bottom_bar()
    
    def _build_top_bar(self):
        """Build the cartridge selection bar"""
        top_frame = ttk.Frame(self.main_frame, style="TFrame")
        top_frame.pack(fill="x", padx=10, pady=(10, 5))
        
        # Cartridge path display (read-only, shows loaded path)
        self.path_var = tk.StringVar(value="No cartridge loaded")
        self.path_label = ttk.Label(
            top_frame,
            textvariable=self.path_var,
            style="Muted.TLabel"
        )
        self.path_label.pack(side="left", fill="x", expand=True, padx=(0, 10))

        # Single Load button (opens picker + loads immediately)
        ttk.Button(
            top_frame,
            text="Load Cartridge...",
            command=self._load_cartridge_dialog,
            style="Action.TButton"
        ).pack(side="left")
    
    def _build_content_area(self):
        """Build the main content area with tabs for traditional and circuit highlighting views"""
        # Create notebook (tabbed interface)
        notebook = ttk.Notebook(self.main_frame, style="TNotebook")
        notebook.pack(fill="both", expand=True, padx=10, pady=5)

        # Tab 1: Traditional view (3-column layout)
        traditional_frame = ttk.Frame(notebook, style="TFrame")
        notebook.add(traditional_frame, text="Traditional View")

        # Configure grid weights
        traditional_frame.columnconfigure(0, weight=1)
        traditional_frame.columnconfigure(1, weight=2)
        traditional_frame.columnconfigure(2, weight=1)
        traditional_frame.rowconfigure(0, weight=1)

        # Left column: Manifest + Stats
        self._build_left_column(traditional_frame)

        # Center column: Query + Results
        self._build_center_column(traditional_frame)

        # Right column: Trace + Graph
        self._build_right_column(traditional_frame)

        # Tab 2: Circuit Highlighting view
        circuit_frame = ttk.Frame(notebook, style="TFrame")
        notebook.add(circuit_frame, text="Circuit Highlighting")

        # Initialize circuit highlighting window
        self.circuit_window = CircuitHighlightingWindow(
            circuit_frame, session_db=self.session_db, app_settings=self.app_settings
        )
    
    def _build_left_column(self, parent):
        """Build the left column (manifest info)"""
        left_frame = ttk.Frame(parent, style="TFrame")
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        
        # Manifest section
        manifest_label = ttk.Label(
            left_frame,
            text="📦 Cartridge Manifest",
            style="Heading.TLabel"
        )
        manifest_label.pack(anchor="w", pady=(0, 5))
        
        # Manifest text area
        self.manifest_text = tk.Text(
            left_frame,
            width=35,
            height=12,
            wrap="word"
        )
        configure_text_widget(self.manifest_text, readonly=True)
        self.manifest_text.pack(fill="x", pady=(0, 10))
        
        # Readiness section
        readiness_label = ttk.Label(
            left_frame,
            text="🔍 Readiness",
            style="Heading.TLabel"
        )
        readiness_label.pack(anchor="w", pady=(0, 5))
        
        self.readiness_text = tk.Text(
            left_frame,
            width=35,
            height=8,
            wrap="word"
        )
        configure_text_widget(self.readiness_text, readonly=True)
        self.readiness_text.pack(fill="x", pady=(0, 10))
        
        # Stats section
        stats_label = ttk.Label(
            left_frame,
            text="📊 Layer Counts",
            style="Heading.TLabel"
        )
        stats_label.pack(anchor="w", pady=(0, 5))
        
        self.stats_text = tk.Text(
            left_frame,
            width=35,
            height=10,
            wrap="word"
        )
        configure_text_widget(self.stats_text, readonly=True)
        self.stats_text.pack(fill="both", expand=True)
    
    def _build_center_column(self, parent):
        """Build the center column (query + results)"""
        center_frame = ttk.Frame(parent, style="TFrame")
        center_frame.grid(row=0, column=1, sticky="nsew", padx=5)
        
        # Query section
        query_label = ttk.Label(
            center_frame,
            text="🔎 Query",
            style="Heading.TLabel"
        )
        query_label.pack(anchor="w", pady=(0, 5))
        
        # Query input frame
        query_input_frame = ttk.Frame(center_frame, style="TFrame")
        query_input_frame.pack(fill="x", pady=(0, 10))
        
        self.query_var = tk.StringVar()
        self.query_entry = ttk.Entry(
            query_input_frame,
            textvariable=self.query_var,
            style="TEntry"
        )
        self.query_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.query_entry.bind("<Return>", lambda e: self._run_walk())
        
        self.walk_button = ttk.Button(
            query_input_frame,
            text="Walk",
            command=self._run_walk,
            style="Action.TButton"
        )
        self.walk_button.pack(side="left")
        
        # Mode selector
        mode_frame = ttk.Frame(center_frame, style="TFrame")
        mode_frame.pack(fill="x", pady=(0, 10))
        
        ttk.Label(mode_frame, text="Mode:", style="TLabel").pack(side="left", padx=(0, 10))
        
        self.mode_var = tk.StringVar(value="semantic-seeded")
        self.mode_combo = ttk.Combobox(
            mode_frame,
            textvariable=self.mode_var,
            values=[
                "semantic-seeded",
                "structure-first",
                "adjacency-heavy",
                "graph-assisted"
            ],
            state="readonly",
            width=20
        )
        self.mode_combo.pack(side="left")
        
        # Results section
        results_label = ttk.Label(
            center_frame,
            text="📄 Results",
            style="Heading.TLabel"
        )
        results_label.pack(anchor="w", pady=(0, 5))
        
        # Results with scrollbar
        results_frame = ttk.Frame(center_frame, style="TFrame")
        results_frame.pack(fill="both", expand=True)
        
        self.results_text = tk.Text(
            results_frame,
            wrap="word"
        )
        configure_text_widget(self.results_text, readonly=False)
        
        results_scroll = ttk.Scrollbar(
            results_frame,
            orient="vertical",
            command=self.results_text.yview
        )
        self.results_text.configure(yscrollcommand=results_scroll.set)
        
        self.results_text.pack(side="left", fill="both", expand=True)
        results_scroll.pack(side="right", fill="y")
        
        # Configure tags for syntax highlighting
        self.results_text.tag_configure("context", foreground=COLORS["action_accent"])
        self.results_text.tag_configure("path", foreground=COLORS["text_secondary"])
        self.results_text.tag_configure("lines", foreground=COLORS["text_muted"])
    
    def _build_right_column(self, parent):
        """Build the right column (trace + stats)"""
        right_frame = ttk.Frame(parent, style="TFrame")
        right_frame.grid(row=0, column=2, sticky="nsew", padx=(5, 0))
        
        # Walk stats section
        stats_label = ttk.Label(
            right_frame,
            text="⏱️ Walk Stats",
            style="Heading.TLabel"
        )
        stats_label.pack(anchor="w", pady=(0, 5))
        
        self.walk_stats_text = tk.Text(
            right_frame,
            width=30,
            height=8,
            wrap="word"
        )
        configure_text_widget(self.walk_stats_text, readonly=True)
        self.walk_stats_text.pack(fill="x", pady=(0, 10))
        
        # Trace section
        trace_label = ttk.Label(
            right_frame,
            text="🛤️ Trace",
            style="Heading.TLabel"
        )
        trace_label.pack(anchor="w", pady=(0, 5))
        
        # Trace tree
        trace_frame = ttk.Frame(right_frame, style="TFrame")
        trace_frame.pack(fill="both", expand=True)
        
        self.trace_tree = ttk.Treeview(
            trace_frame,
            columns=("operator", "score"),
            show="tree headings",
            height=15
        )
        self.trace_tree.heading("#0", text="Target")
        self.trace_tree.heading("operator", text="Op")
        self.trace_tree.heading("score", text="Score")
        
        self.trace_tree.column("#0", width=120)
        self.trace_tree.column("operator", width=60)
        self.trace_tree.column("score", width=50)
        
        trace_scroll = ttk.Scrollbar(
            trace_frame,
            orient="vertical",
            command=self.trace_tree.yview
        )
        self.trace_tree.configure(yscrollcommand=trace_scroll.set)
        
        self.trace_tree.pack(side="left", fill="both", expand=True)
        trace_scroll.pack(side="right", fill="y")
    
    def _build_bottom_bar(self):
        """Build the status bar"""
        bottom_frame = ttk.Frame(self.main_frame, style="TFrame")
        bottom_frame.pack(fill="x", padx=10, pady=10)
        
        # Status label
        self.status_var = tk.StringVar(value="Ready. Load a cartridge to begin.")
        self.status_label = ttk.Label(
            bottom_frame,
            textvariable=self.status_var,
            style="Muted.TLabel"
        )
        self.status_label.pack(side="left")
        
        # Version
        ttk.Label(
            bottom_frame,
            text="Node Walker v3.0.0",
            style="Muted.TLabel"
        ).pack(side="right")
    
    # =========================================================================
    # Actions
    # =========================================================================
    
    def _load_cartridge_dialog(self):
        """Open file picker and immediately load the selected cartridge."""
        path = filedialog.askopenfilename(
            title="Select Tripartite Cartridge",
            filetypes=[
                ("SQLite Database", "*.db"),
                ("All Files", "*.*")
            ]
        )
        if not path:
            return  # User cancelled

        path = Path(path)
        if not path.exists():
            messagebox.showerror("Error", f"File not found: {path}")
            return

        try:
            # Close existing connection
            if self.db:
                self.db.close()

            # Connect
            self.db = CartridgeDB()
            self.db.connect(path)
            self.cartridge_path = path
            self.path_var.set(str(path))

            # Initialize session DB and activation store
            self.session_db = SessionDB()
            self.activation_store = ActivationStore()

            # Create walker with activation tracking
            config = WalkerConfig(
                session_db=self.session_db,
                activation_store=self.activation_store
            )
            self.walker = NodeWalker(self.db, config=config)

            # Wire up circuit highlighting window
            if self.circuit_window:
                self.circuit_window.session_db = self.session_db
                self.circuit_window.activation_store = self.activation_store
                self.circuit_window.airlock_pane.session_db = self.session_db
                self.circuit_window.llm_agent = LLMAgent(
                    model=self.app_settings.big_brain.model_name,
                    helper_model=self.app_settings.helper.model_name,
                    session_db=self.session_db,
                )
                self.circuit_window.on_cartridge_loaded(
                    db=self.db,
                    walker=self.walker
                )

            # Assess readiness
            readiness = self.walker.assess_readiness()

            # Update traditional view UI
            self._display_manifest()
            self._display_readiness(readiness)
            self._display_stats()

            self._set_status(f"Loaded: {path.name}", "success")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to load cartridge:\n{e}")
            self._set_status(f"Error: {e}", "error")
    
    def _run_walk(self):
        """Execute a traversal walk"""
        if not self.walker:
            messagebox.showwarning("Warning", "Please load a cartridge first.")
            return
        
        query = self.query_var.get().strip()
        if not query:
            messagebox.showwarning("Warning", "Please enter a query.")
            return
        
        # Disable button during walk
        self.walk_button.configure(state="disabled")
        self._set_status("Walking...", "info")
        
        # Clear previous results
        self.results_text.configure(state="normal")
        self.results_text.delete("1.0", "end")
        self.results_text.configure(state="disabled")
        
        # Run in background thread
        def do_walk():
            try:
                # Set mode
                mode_str = self.mode_var.get()
                mode_map = {
                    "semantic-seeded": TraversalMode.SEMANTIC_SEEDED,
                    "structure-first": TraversalMode.STRUCTURE_FIRST,
                    "adjacency-heavy": TraversalMode.ADJACENCY_HEAVY,
                    "graph-assisted": TraversalMode.GRAPH_ASSISTED,
                }
                mode = mode_map.get(mode_str, TraversalMode.SEMANTIC_SEEDED)
                
                self.walker.config.policy.mode = mode
                
                # Execute walk
                artifact = self.walker.walk(query=query)
                
                # Send results to main thread
                self.msg_queue.put(("walk_complete", artifact))
                
            except Exception as e:
                self.msg_queue.put(("walk_error", str(e)))
        
        thread = threading.Thread(target=do_walk, daemon=True)
        thread.start()
    
    def _process_messages(self):
        """Process messages from background threads"""
        try:
            while True:
                msg_type, data = self.msg_queue.get_nowait()
                
                if msg_type == "walk_complete":
                    self._display_walk_results(data)
                    self._set_status(
                        f"Walk complete: {data.total_chunks} chunks in {data.elapsed_ms}ms",
                        "success"
                    )
                    self.walk_button.configure(state="normal")
                    
                elif msg_type == "walk_error":
                    messagebox.showerror("Walk Error", data)
                    self._set_status(f"Error: {data}", "error")
                    self.walk_button.configure(state="normal")

                elif msg_type == "model_validation":
                    self._handle_model_validation(data)

        except queue.Empty:
            pass
        
        # Schedule next check
        self.root.after(100, self._process_messages)
    
    # =========================================================================
    # Display Methods
    # =========================================================================
    
    def _display_manifest(self):
        """Display cartridge manifest info"""
        if not self.db:
            return
        
        reader = ManifestReader(self.db)
        manifest = reader.manifest
        
        self.manifest_text.configure(state="normal")
        self.manifest_text.delete("1.0", "end")
        
        if manifest:
            lines = [
                f"ID: {manifest.cartridge_id[:16]}...",
                f"Schema: v{manifest.schema_ver}",
                f"Pipeline: {manifest.pipeline_ver}",
                f"",
                f"Source: {manifest.source_root}",
                f"",
                f"Embed Model: {manifest.embed_model}",
                f"Embed Dims: {manifest.embed_dims}",
                f"",
                f"Deployable: {'✓' if manifest.is_deployable else '✗'}",
            ]
            self.manifest_text.insert("1.0", "\n".join(lines))
        else:
            self.manifest_text.insert("1.0", "No manifest found")
        
        self.manifest_text.configure(state="disabled")
    
    def _display_readiness(self, readiness):
        """Display readiness assessment"""
        self.readiness_text.configure(state="normal")
        self.readiness_text.delete("1.0", "end")
        
        level_icons = {
            ReadinessLevel.READY: "✓ READY",
            ReadinessLevel.DEGRADED: "⚠ DEGRADED",
            ReadinessLevel.BLOCKED: "✗ BLOCKED",
        }
        
        lines = [
            f"Status: {level_icons.get(readiness.level, '?')}",
            "",
            f"Structure: {'✓' if readiness.can_use_structure else '✗'}",
            f"Semantic:  {'✓' if readiness.can_use_semantic else '✗'}",
            f"Graph:     {'✓' if readiness.can_use_graph else '✗'}",
            f"FTS:       {'✓' if readiness.can_use_fts else '✗'}",
        ]
        
        if readiness.blockers:
            lines.append("")
            lines.append("Blockers:")
            for b in readiness.blockers[:3]:
                lines.append(f"  • {b[:40]}")
        
        self.readiness_text.insert("1.0", "\n".join(lines))
        self.readiness_text.configure(state="disabled")
    
    def _display_stats(self):
        """Display layer counts"""
        if not self.db:
            return
        
        reader = ManifestReader(self.db)
        counts = reader.get_telemetry()
        
        self.stats_text.configure(state="normal")
        self.stats_text.delete("1.0", "end")
        
        lines = [
            "Verbatim Layer:",
            f"  verbatim_lines: {counts.get('verbatim_lines', 0):,}",
            f"  source_files:   {counts.get('source_files', 0):,}",
            "",
            "Structural Layer:",
            f"  tree_nodes:     {counts.get('tree_nodes', 0):,}",
            "",
            "Semantic Layer:",
            f"  chunk_manifest: {counts.get('chunk_manifest', 0):,}",
            f"  embeddings:     {counts.get('embeddings', 0):,}",
            "",
            "Knowledge Graph:",
            f"  graph_nodes:    {counts.get('graph_nodes', 0):,}",
            f"  graph_edges:    {counts.get('graph_edges', 0):,}",
        ]
        
        self.stats_text.insert("1.0", "\n".join(lines))
        self.stats_text.configure(state="disabled")
    
    def _display_walk_results(self, artifact):
        """Display walk results"""
        # Results text
        self.results_text.configure(state="normal")
        self.results_text.delete("1.0", "end")
        
        for i, block in enumerate(artifact.content_blocks):
            if isinstance(block, dict):
                if "error" in block:
                    self.results_text.insert("end", f"Error: {block['error']}\n")
                    continue
                
                # Context prefix
                ctx = block.get("context_prefix", "")
                if ctx:
                    self.results_text.insert("end", f"▸ {ctx}\n", "context")
                
                # File path and lines
                path = block.get("file_path", "")
                lines = block.get("lines", "")
                if path:
                    self.results_text.insert("end", f"  {path}", "path")
                    if lines:
                        self.results_text.insert("end", f" [{lines}]", "lines")
                    self.results_text.insert("end", "\n")
                
                # Content
                content = block.get("content", "")
                if content:
                    # Truncate long content
                    if len(content) > 500:
                        content = content[:500] + "..."
                    self.results_text.insert("end", f"{content}\n")
                
                self.results_text.insert("end", "\n")
        
        self.results_text.configure(state="disabled")
        
        # Walk stats
        self.walk_stats_text.configure(state="normal")
        self.walk_stats_text.delete("1.0", "end")
        
        stats_lines = [
            f"Query: {artifact.query[:30]}...",
            f"Mode: {artifact.mode.value}",
            "",
            f"Chunks: {artifact.total_chunks}",
            f"Nodes: {artifact.total_nodes}",
            f"Lines: {artifact.total_lines}",
            f"Time: {artifact.elapsed_ms}ms",
            "",
            f"Seeds: {len(artifact.seeds)}",
        ]
        
        self.walk_stats_text.insert("1.0", "\n".join(stats_lines))
        self.walk_stats_text.configure(state="disabled")
        
        # Trace tree
        for item in self.trace_tree.get_children():
            self.trace_tree.delete(item)
        
        if artifact.trace:
            for step in artifact.trace.steps[:50]:  # Limit to 50
                self.trace_tree.insert(
                    "",
                    "end",
                    text=step.target_id[:16] + "...",
                    values=(step.operator.value[:8], f"{step.score:.2f}")
                )
    
    def _set_status(self, message: str, level: str = "info"):
        """Update status bar"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.status_var.set(f"[{timestamp}] {message}")
        
        if level == "success":
            self.status_label.configure(style="Success.TLabel")
        elif level == "error":
            self.status_label.configure(style="Error.TLabel")
        else:
            self.status_label.configure(style="Muted.TLabel")


    # =========================================================================
    # Model Validation
    # =========================================================================

    def _validate_models_background(self):
        """Validate model slots against Ollama on startup (non-blocking)."""
        import threading

        def validate():
            result = validate_models(self.app_settings)
            self.msg_queue.put(("model_validation", result))

        threading.Thread(target=validate, daemon=True).start()

    def _handle_model_validation(self, result):
        """Handle model validation result on the main thread."""
        if result.all_ok:
            self._set_status(f"Models OK: {result.summary}", "success")
        else:
            self._set_status(f"Model issue: {result.summary}", "error")
            # Show warning but don't block — user can fix via settings
            messagebox.showwarning(
                "Model Setup Required",
                f"Not all model slots are operational:\n\n"
                f"{result.summary}\n\n"
                f"Open Settings in the Circuit Highlighting tab to configure models.\n"
                f"Both a Big Brain and Helper model are required for forensic mode."
            )


def run_app():
    """Entry point for the application"""
    root = tk.Tk()
    app = NodeWalkerApp(root)
    root.mainloop()
