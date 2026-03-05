"""
Node Walker - Main Application Entry Point
Run with: python -m src.app
"""

import sys
from pathlib import Path

# Ensure src is in path when running as module
if __name__ == "__main__":
    # Add project root to path
    project_root = Path(__file__).parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from src.ui.main_window import run_app


def main():
    """Main entry point"""
    run_app()


if __name__ == "__main__":
    main()
