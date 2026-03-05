# Node Walker

**Tripartite Cartridge Traversal Engine - Systems Thinker Edition**

A production-grade traversal engine for Tripartite DataSTORE cartridges with multi-gradient exploration and decision memory.

## Quick Start

```bash
# Setup (Windows)
setup_env.bat

# Or manually
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# Run application
python -m src.app

# Run tests
python -m src.test_walker
```

## Architecture

```
_NodeWALKER/
├── 📁 assets/icons/
├── 📁 src/
│   ├── 📁 walker/          # Core traversal engine
│   │   ├── types.py        # All dataclasses
│   │   ├── db.py           # SQLite connection
│   │   ├── manifest.py     # Readiness checks
│   │   ├── cas.py          # CAS resolution
│   │   ├── structure.py    # Tree operators
│   │   ├── chunks.py       # Chunk operators
│   │   ├── graph.py        # Graph operators
│   │   ├── scoring.py      # S_total formula
│   │   ├── walker.py       # Main orchestrator
│   │   ├── policy.py       # Policy selection
│   │   ├── notes.py        # Decision memory
│   │   ├── signature.py    # Fingerprinting
│   │   └── antidata.py     # Rule engine
│   ├── 📁 ui/              # Tkinter interface
│   │   ├── theme.py        # THEME_SPEC colors
│   │   └── main_window.py  # Main UI
│   └── app.py              # Entry point
├── README.md
├── requirements.txt
└── setup_env.bat
```

## Features

### Multi-Gradient Traversal
- **Structural**: Parent/children/siblings via tree_nodes
- **Adjacency**: Chunk continuity via overlap chains
- **Semantic**: Vector similarity (when embeddings available)
- **Graph**: Knowledge graph relationships

### Scoring Formula
```
S_total = w1*S_sem + w2*S_struct + w3*S_adj + w4*S_graph + w5*S_source
          - dup_penalty - anti_data_penalty
```

### Decision Memory
- Notes database for operational learning
- Anti-data rules (block/penalize/warn)
- Cartridge profile matching via signature hash

### Output Contract
Every walk produces a `TraversalArtifact` with:
- Collected content with full provenance
- Deterministic trace for reproducibility
- Explicit traversal paths

## Usage

### GUI Application
```bash
python -m src.app
```

### Programmatic API
```python
from src.walker import CartridgeDB, NodeWalker, WalkerConfig

# Connect to cartridge
db = CartridgeDB()
db.connect(Path("my_cartridge.db"))

# Create walker and assess readiness
walker = NodeWalker(db)
readiness = walker.assess_readiness()

if readiness.level != ReadinessLevel.BLOCKED:
    # Execute walk
    artifact = walker.walk(query="find authentication")
    
    for block in artifact.content_blocks:
        print(block["context_prefix"])
        print(block["content"])
```

## Theme

Follows THEME_SPEC.md:
- Primary Background: `#1e1e2f`
- Secondary Background: `#151521`
- Action Accent: `#007ACC`
- Status Accent: `#00FF00`

## License

MIT
