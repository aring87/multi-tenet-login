"""Repository resources and persistent data, independent of the working directory."""
import os
import sys
from pathlib import Path


def _data_folder(base):
    """desktop-data beside the app; if that folder can't be written (for example under
    Program Files), fall back to the user's local app data."""
    folder = base / "desktop-data"
    try:
        folder.mkdir(exist_ok=True)
        probe = folder / ".write-test"
        probe.write_text("", encoding="utf-8"); probe.unlink()
        return folder
    except OSError:
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Sentinel Workspace" / "desktop-data"


if getattr(sys, "frozen", False):
    # Packaged .exe: bundled docs/templates are unpacked to a temporary folder that is
    # deleted on exit, so saved data must live beside the .exe instead.
    ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    DATA = _data_folder(Path(sys.executable).resolve().parent)
else:
    ROOT = Path(__file__).resolve().parent.parent
    # Preserve existing clients, settings, sessions and onboarding state after upgrades.
    DATA = ROOT / "desktop-data"
TEMPLATES = ROOT / "templates"
DOCS = ROOT / "docs"
