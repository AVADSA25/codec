"""CODEC Pilot — browser automation pillar."""
from .pilot_chrome import PilotChrome, pilot_session
from .snapshot import take_snapshot, render_for_llm, PageSnapshot, IndexedElement

__all__ = [
    "PilotChrome",
    "pilot_session",
    "take_snapshot",
    "render_for_llm",
    "PageSnapshot",
    "IndexedElement",
]
