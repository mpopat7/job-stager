"""Google Sheets tracker connector for the configured Applications spreadsheet."""

from __future__ import annotations

import datetime
import logging
import os
from pathlib import Path
import subprocess
from typing import List, Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build

from core.scrapers.base import JobPosting
from core.tracker.base import BaseTracker
from core.tracker.local_db import LocalTracker

logger = logging.getLogger(__name__)

# Tracker defaults. The id comes from profile.yaml or the environment; never hardcoded.
DEFAULT_SPREADSHEET_ID = os.environ.get("JOBSTAGER_SPREADSHEET_ID", "")
DEFAULT_KEY_PATH = os.path.expanduser("~/.config/gcp/sheets-bot.json")
DEFAULT_APPLICATIONS_TAB = "Applications"
COLS = 11
HEADERS = [
    "Company",
    "Role",
    "Location",
    "Source",
    "Date Applied",
    "Stage",
    "Next Action",
    "Next Action Date",
    "Comp/Notes",
    "Link",
    "Grad Year Used",
]


class SheetsTracker(BaseTracker):
    """Synchronizes application staging and submissions to a Google Sheets tracker."""

    def __init__(
        self,
        spreadsheet_id: Optional[str] = None,
        key_path: Optional[str] = None,
        tab_name: Optional[str] = None,
    ):
        self.spreadsheet_id = spreadsheet_id or DEFAULT_SPREADSHEET_ID
        if not self.spreadsheet_id:
            raise ValueError(
                "No spreadsheet id. Set tracker.spreadsheet_id in profile.yaml or "
                "the JOBSTAGER_SPREADSHEET_ID environment variable."
            )
        self.key_path = os.path.expanduser(key_path or DEFAULT_KEY_PATH)
        self.tab_name = tab_name or DEFAULT_APPLICATIONS_TAB
        self.local_tracker = LocalTracker()

    def _get_service(self):
        """Build Google Sheets API service using service-account credentials."""
        if not os.path.exists(self.key_path):
            raise FileNotFoundError(f"Service account key not found at {self.key_path}")
        creds = service_account.Credentials.from_service_account_file(
            self.key_path,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        return build("sheets", "v4", credentials=creds).spreadsheets()

    def read_applications(self) -> List[dict]:
        """Read all applications from Google Sheet into a list of row dicts."""
        svc = self._get_service()
        res = (
            svc.values()
            .get(spreadsheetId=self.spreadsheet_id, range=f"{self.tab_name}!A2:K")
            .execute()
        )
        rows = res.get("values", [])
        out = []
        for i, r in enumerate(rows, start=2):
            padded = r + [""] * (COLS - len(r))
            if not padded[0].strip():
                continue
            entry = dict(zip(HEADERS, padded))
            entry["row"] = i
            out.append(entry)
        return out

    def _date_key(self, row: List[str]) -> Optional[datetime.date]:
        raw = row[4].strip() if len(row) > 4 else ""
        for fmt in ("%d %b %Y", "%Y-%m-%d"):
            try:
                return datetime.datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        return None

    def sort_applications(self, svc) -> None:
        """Reorder Applications tab rows by Date Applied (most recent first) with OVERWRITE."""
        try:
            res = (
                svc.values()
                .get(spreadsheetId=self.spreadsheet_id, range=f"{self.tab_name}!A2:K")
                .execute()
            )
            rows = res.get("values", [])
            if not rows:
                return
            rows = [r + [""] * (COLS - len(r)) for r in rows]
            rows.sort(key=lambda r: self._date_key(r) or datetime.date.min, reverse=True)
            svc.values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f"{self.tab_name}!A2:K{len(rows) + 1}",
                valueInputOption="USER_ENTERED",
                body={"values": rows},
            ).execute()
        except Exception as e:
            logger.error(f"Error sorting applications in Google Sheet: {e}")

    def log_application(
        self,
        job: JobPosting,
        grad_year: int,
        stage: str = "Applied",
        notes: str = "",
        next_action: str = "",
        next_date: str = "",
    ) -> bool:
        """Log the application into the configured Google Sheets tracker."""
        # 1. Always record in local SQLite database
        self.local_tracker.log_application(
            job=job,
            grad_year=grad_year,
            stage=stage,
            notes=notes,
        )

        # 2. Sync to Google Sheets doc
        today_formatted = datetime.date.today().strftime("%-d %b %Y")  # e.g. "6 Sep 2026"
        row = [
            job.company,
            job.title,
            job.location,
            job.provider.value.title(),
            today_formatted,
            stage,
            next_action,
            next_date,
            notes,
            job.url,
            str(grad_year),
        ]

        try:
            svc = self._get_service()
            # Append with OVERWRITE to prevent shifting formula ranges or data validations
            svc.values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"{self.tab_name}!A1",
                valueInputOption="USER_ENTERED",
                insertDataOption="OVERWRITE",
                body={"values": [row]},
            ).execute()

            # Re-sort data block in place so newest lands at top
            self.sort_applications(svc)
            logger.info(f"Successfully logged to Google Sheet '{self.spreadsheet_id}': {job.company} - {job.title}")
            return True

        except Exception as e:
            logger.warning(f"Direct Google Sheets API sync encountered error: {e}. Trying internship-watcher fallback...")
            # Fallback to internship-watcher script if available
            watcher_dir = Path.home() / "dev" / "projects" / "sandbox" / "internship-watcher"
            if (watcher_dir / "add.py").exists():
                try:
                    cmd = [
                        "uv", "run", "--with", "google-api-python-client", "--with", "google-auth",
                        "python3", "add.py",
                        "--company", job.company,
                        "--role", job.title,
                        "--location", job.location,
                        "--source", job.provider.value.title(),
                        "--link", job.url,
                        "--stage", stage,
                        "--grad-year", str(grad_year),
                    ]
                    if notes:
                        cmd.extend(["--notes", notes])
                    res = subprocess.run(cmd, cwd=str(watcher_dir), capture_output=True, text=True, check=False)
                    return res.returncode == 0
                except Exception as ex:
                    logger.error(f"Fallback to internship-watcher failed: {ex}")
            return False
