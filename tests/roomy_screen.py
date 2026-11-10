"""Layout tests measure the app at the window size they ask for, whatever screen the PC has.

A CI runner's screen (1024x768 at 100%) is smaller than the app's minimum window: the app then
clamps its minimum size to the work area, and Tk caps the window at the screen (its default
maxsize), so the layout under test is not the one the test set. Tests that check layout at a
given size call roomy_screen() before building the App and roomy_window() on its root."""
from unittest.mock import patch

from sentinel_app import desktop_app as ui
from sentinel_app import ui_kit as kit

AREA = (0, 0, 7680, 4320)


def roomy_screen(test):
    """A large work area for the rest of ``test`` (the main window and dialogs read it)."""
    for module in (ui, kit):
        p = patch.object(module, "work_area", lambda *args, **kwargs: AREA)
        p.start(); test.addCleanup(p.stop)


def roomy_window(root):
    """Let the window grow past the physical screen, so its geometry is what the test set."""
    root.maxsize(AREA[2], AREA[3])
