"""Google Sheets integration service for saving confirmed registrations."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo
import gspread
from google.oauth2.service_account import Credentials

from app.config import mask_phone

logger = logging.getLogger(__name__)

EXPECTED_HEADERS = [
    "registered_at",
    "telegram_user_id",
    "first_name",
    "last_name",
    "phone_number",
    "registration_status",
]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class GoogleSheetsService:
    """Manages appending confirmed registrations to Google Sheets."""

    def __init__(
        self,
        spreadsheet_id: str,
        tab_name: str = "Registrations",
        service_account_file: str = "credentials/service-account.json",
        timezone_name: str = "Asia/Tashkent",
    ) -> None:
        self.spreadsheet_id = spreadsheet_id
        self.tab_name = tab_name
        self.service_account_file = service_account_file
        self.timezone_name = timezone_name
        self._client: gspread.Client | None = None
        self._worksheet: gspread.Worksheet | None = None
        self._cache_records: Dict[int, Dict[str, Any]] = {}
        self._cache_time: float = 0.0

    def initialize(self) -> None:
        """Authenticate and verify access to the spreadsheet and worksheet."""
        sa_path = Path(self.service_account_file)
        if not sa_path.exists():
            raise FileNotFoundError(
                f"Google Service Account file not found at: {self.service_account_file}. "
                "Ensure credentials are configured properly."
            )

        try:
            credentials = Credentials.from_service_account_file(
                str(sa_path),
                scopes=SCOPES,
            )
            self._client = gspread.authorize(credentials)
            spreadsheet = self._client.open_by_key(self.spreadsheet_id)

            # Try to select the specified worksheet, or create if it does not exist
            try:
                self._worksheet = spreadsheet.worksheet(self.tab_name)
            except gspread.exceptions.WorksheetNotFound:
                logger.info(f"Worksheet '{self.tab_name}' not found. Creating it...")
                self._worksheet = spreadsheet.add_worksheet(
                    title=self.tab_name, rows=1000, cols=len(EXPECTED_HEADERS)
                )

            # Verify or write headers
            first_row = self._worksheet.row_values(1)
            if not first_row or first_row != EXPECTED_HEADERS:
                if not first_row:
                    logger.info("Initializing empty worksheet with standard headers.")
                    self._worksheet.append_row(EXPECTED_HEADERS)
                else:
                    logger.warning(
                        f"Worksheet headers differ from expected. Existing: {first_row}"
                    )

            logger.info(
                f"Successfully connected to Google Sheet '{spreadsheet.title}' (tab: '{self.tab_name}')."
            )
            self._refresh_cache(force=True)

        except Exception as e:
            logger.error(f"Failed to initialize Google Sheets service: {e}")
            raise

    def _refresh_cache(self, force: bool = False) -> None:
        """Fetch all rows from Google Sheets and cache them for fast lookup."""
        if not force and self._cache_records and (time.time() - self._cache_time < 30.0):
            return

        if not self._worksheet:
            return

        try:
            all_rows = self._worksheet.get_all_values()
            new_cache: Dict[int, Dict[str, Any]] = {}
            for idx, row in enumerate(all_rows[1:], start=2):
                if len(row) > 1 and row[1].strip().isdigit():
                    uid = int(row[1].strip())
                    new_cache[uid] = {
                        "row_index": idx,
                        "registered_at": row[0] if len(row) > 0 else "",
                        "telegram_user_id": uid,
                        "first_name": row[2] if len(row) > 2 else "",
                        "last_name": row[3] if len(row) > 3 else "",
                        "phone_number": row[4] if len(row) > 4 else "",
                        "status": row[5] if len(row) > 5 else "confirmed",
                    }
            self._cache_records = new_cache
            self._cache_time = time.time()
        except Exception as e:
            logger.warning(f"Could not refresh Google Sheets cache: {e}")

    def find_user_row(self, telegram_user_id: int) -> int | None:
        """Find 1-based row index for telegram_user_id, or None if not found."""
        self._refresh_cache()
        record = self._cache_records.get(telegram_user_id)
        return record["row_index"] if record else None

    def get_registration(self, telegram_user_id: int) -> Optional[Dict[str, Any]]:
        """Get existing registration dictionary for telegram_user_id, or None if not registered."""
        self._refresh_cache()
        return self._cache_records.get(telegram_user_id)

    def is_user_registered(self, telegram_user_id: int) -> bool:
        """Check if telegram_user_id already has a row in the sheet."""
        return self.get_registration(telegram_user_id) is not None

    def save_registration(
        self,
        telegram_user_id: int,
        first_name: str,
        last_name: str,
        phone_number: str,
    ) -> bool:
        """
        Append or update a confirmed registration row in Google Sheets.
        Returns True if successful, False otherwise.
        """
        if not self._worksheet:
            self.initialize()

        assert self._worksheet is not None

        try:
            # Prepare timestamp in Tashkent timezone
            try:
                tz = ZoneInfo(self.timezone_name)
            except Exception:
                tz = ZoneInfo("Asia/Tashkent")

            registered_at = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

            row_data = [
                registered_at,
                str(telegram_user_id),
                first_name.strip(),
                last_name.strip(),
                phone_number.strip(),
                "confirmed",
            ]

            existing_row = self.find_user_row(telegram_user_id)
            if existing_row:
                logger.info(
                    f"User {telegram_user_id} already registered. Updating existing row {existing_row} in Google Sheets."
                )
                self._worksheet.update(values=[row_data], range_name=f"A{existing_row}:F{existing_row}")
                self._refresh_cache(force=True)
                logger.info(f"Successfully updated registration row {existing_row} for user {telegram_user_id}.")
                return True
            else:
                logger.info(
                    f"Writing new registration for user {telegram_user_id} "
                    f"({first_name} {last_name}, phone: {mask_phone(phone_number)})"
                )
                self._worksheet.append_row(row_data)
                self._refresh_cache(force=True)
                logger.info(f"Successfully appended registration for user {telegram_user_id}.")
                return True

        except Exception as e:
            logger.error(
                f"Failed to write registration to Google Sheets for user {telegram_user_id}: {e}"
            )
            return False
