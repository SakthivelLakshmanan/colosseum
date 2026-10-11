"""Finance adviser ADK web application entry point."""
from pathlib import Path
import sys

# Ensure repository root is on sys.path
root_dir = str(Path(__file__).resolve().parent.parent.parent)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from agents.adviser_factory import build_adviser

# Root agent exposed for ADK web and CLI
root_agent = build_adviser("finance")
