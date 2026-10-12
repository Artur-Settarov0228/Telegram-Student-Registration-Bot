"""Configuration and access control management for the Telegram Registration Userbot."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Set
from dotenv import load_dotenv

# Automatically load .env file from project root
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=BASE_DIR / ".env")


def _parse_id_set(raw_value: str | None) -> Set[int]:
    """Parse comma-separated Telegram user IDs into a set of integers."""
    if not raw_value:
        return set()
    result = set()
    for item in raw_value.split(","):
        cleaned = item.strip()
        if not cleaned:
            continue
        try:
            result.add(int(cleaned))
        except ValueError:
            pass
    return result


class AccessController:
    """Deterministic access control and contact exclusion manager."""

    def __init__(
        self,
        excluded_ids: Set[int] | None = None,
        allowed_ids: Set[int] | None = None,
    ) -> None:
        self.excluded_ids: Set[int] = set(excluded_ids) if excluded_ids else set()
        self.allowed_ids: Set[int] = set(allowed_ids) if allowed_ids else set()

    def is_excluded(self, user_id: int) -> bool:
        """Check if a Telegram user ID is explicitly excluded."""
        return user_id in self.excluded_ids

    def is_allowed(self, user_id: int) -> bool:
        """
        Check if user is allowed to interact with the bot.

        Rules:
        1. If user is in excluded list, always return False.
        2. If allowed list is configured and non-empty, user must be in allowed list.
        3. Otherwise, return True.
        """
        if self.is_excluded(user_id):
            return False
        if self.allowed_ids and user_id not in self.allowed_ids:
            return False
        return True

    def add_excluded(self, user_id: int) -> None:
        """Dynamically add a user ID to the exclusion list."""
        self.excluded_ids.add(user_id)

    def remove_excluded(self, user_id: int) -> None:
        """Dynamically remove a user ID from the exclusion list."""
        self.excluded_ids.discard(user_id)

    def add_allowed(self, user_id: int) -> None:
        """Dynamically add a user ID to the allowlist."""
        self.allowed_ids.add(user_id)

    def remove_allowed(self, user_id: int) -> None:
        """Dynamically remove a user ID from the allowlist."""
        self.allowed_ids.discard(user_id)


@dataclass
class Config:
    """Application configuration loaded from environment variables."""

    # Telegram Credentials
    tg_api_id: int
    tg_api_hash: str
    tg_phone: str
    tg_session_name: str = "userbot"

    # Google Sheets Configuration
    google_sheets_id: str = ""
    google_sheets_tab_name: str = "Registrations"
    google_service_account_file: str = "credentials/service-account.json"

    # Timezone
    timezone: str = "Asia/Tashkent"

    # AI Configuration (Optional)
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str | None = None

    # Access Control
    access_controller: AccessController = field(default_factory=AccessController)

    @classmethod
    def load(cls) -> Config:
        """Load and validate configuration from environment."""
        tg_api_id_raw = os.getenv("TG_API_ID", "").strip()
        tg_api_hash = os.getenv("TG_API_HASH", "").strip()
        tg_phone = os.getenv("TG_PHONE", "").strip()

        errors = []
        if not tg_api_id_raw:
            errors.append("TG_API_ID is required")
        else:
            try:
                tg_api_id = int(tg_api_id_raw)
            except ValueError:
                errors.append("TG_API_ID must be a valid integer")
                tg_api_id = 0

        if not tg_api_hash:
            errors.append("TG_API_HASH is required")
        if not tg_phone:
            errors.append("TG_PHONE is required")

        google_sheets_id = os.getenv("GOOGLE_SHEETS_ID", "").strip()
        if not google_sheets_id:
            errors.append("GOOGLE_SHEETS_ID is required")

        if errors:
            raise ValueError(f"Configuration error:\n - " + "\n - ".join(errors))

        google_tab = os.getenv("GOOGLE_SHEETS_TAB_NAME", "Registrations").strip() or "Registrations"
        service_account_file = os.getenv(
            "GOOGLE_SERVICE_ACCOUNT_FILE", "credentials/service-account.json"
        ).strip() or "credentials/service-account.json"
        
        # If relative path, resolve against project base directory
        if not os.path.isabs(service_account_file):
            service_account_file = str(BASE_DIR / service_account_file)

        timezone = os.getenv("TIMEZONE", "Asia/Tashkent").strip() or "Asia/Tashkent"

        excluded_ids = _parse_id_set(os.getenv("EXCLUDED_USER_IDS"))
        allowed_ids = _parse_id_set(os.getenv("ALLOWED_USER_IDS"))
        access_controller = AccessController(excluded_ids=excluded_ids, allowed_ids=allowed_ids)

        openai_key = os.getenv("OPENAI_API_KEY", "").strip() or None
        openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
        openai_base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None

        session_name = os.getenv("TG_SESSION_NAME", "userbot").strip() or "userbot"

        return cls(
            tg_api_id=tg_api_id,
            tg_api_hash=tg_api_hash,
            tg_phone=tg_phone,
            tg_session_name=session_name,
            google_sheets_id=google_sheets_id,
            google_sheets_tab_name=google_tab,
            google_service_account_file=service_account_file,
            timezone=timezone,
            openai_api_key=openai_key,
            openai_model=openai_model,
            openai_base_url=openai_base_url,
            access_controller=access_controller,
        )


def mask_phone(phone: str | None) -> str:
    """Mask phone number for safe logging (e.g. +998*****1234)."""
    if not phone:
        return "<none>"
    digits = "".join(c for c in phone if c.isdigit() or c == "+")
    if len(digits) <= 5:
        return "***"
    return f"{digits[:4]}*****{digits[-4:]}"
