"""Repository resources and persistent data, independent of the working directory."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
DOCS = ROOT / "docs"
# Preserve existing clients, settings, sessions and onboarding state after upgrades.
DATA = ROOT / "desktop-data"
