"""Application tracking connectors."""

from core.tracker.base import BaseTracker
from core.tracker.local_db import LocalTracker
from core.tracker.matches import MatchStore
from core.tracker.sheets import SheetsTracker


def tracker_for(user_id, profile):
    """Use Sheets when configured; otherwise keep the in-app tracker self-contained."""
    if profile.tracker.spreadsheet_id and profile.tracker.auto_sync_sheets:
        return SheetsTracker(
            user_id,
            spreadsheet_id=profile.tracker.spreadsheet_id,
            key_path=profile.tracker.credentials_path,
            tab_name=profile.tracker.sheet_tab,
        )
    return LocalTracker(user_id)

__all__ = [
    "BaseTracker",
    "LocalTracker",
    "MatchStore",
    "SheetsTracker",
    "tracker_for",
]
