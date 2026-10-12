"""Entrypoint for the Telegram Student Registration Userbot."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path

from app.agent import RegistrationAgent
from app.config import Config
from app.google_sheets import GoogleSheetsService
from app.registration import ConversationManager
from app.telegram_client import UserbotService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("telegram_userbot")


async def main() -> None:
    """Initialize components and start the userbot."""
    logger.info("Initializing Telegram Registration Userbot...")

    try:
        config = Config.load()
    except Exception as e:
        logger.error(f"Configuration error: {e}")
        logger.info(
            "Please copy .env.example to .env and fill in required environment variables."
        )
        sys.exit(1)

    # Initialize Google Sheets Service
    google_sheets = GoogleSheetsService(
        spreadsheet_id=config.google_sheets_id,
        tab_name=config.google_sheets_tab_name,
        service_account_file=config.google_service_account_file,
        timezone_name=config.timezone,
    )

    sa_file = Path(config.google_service_account_file)
    if sa_file.exists():
        try:
            google_sheets.initialize()
            logger.info("Google Sheets integration verified.")
        except Exception as e:
            logger.warning(
                f"Google Sheets connection could not be verified on startup: {e}. "
                "Will attempt reconnect on first registration write."
            )
    else:
        logger.warning(
            f"Google service account file '{config.google_service_account_file}' not found. "
            "Please ensure the file exists before users confirm registrations."
        )

    # Initialize conversation state manager and AI agent
    conversation_manager = ConversationManager()
    agent = RegistrationAgent(config)

    # Initialize Telegram Userbot service
    userbot = UserbotService(
        config=config,
        conversation_manager=conversation_manager,
        agent=agent,
        google_sheets=google_sheets,
    )

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def signal_handler() -> None:
        logger.info("Shutdown signal received. Stopping userbot...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # On Windows or environments without full signal support
            pass

    try:
        await userbot.start()
        logger.info("Telegram Userbot is running and listening for registration messages.")

        # Wait until shutdown signal or client disconnects
        run_task = asyncio.create_task(userbot.run_until_disconnected())
        stop_task = asyncio.create_task(stop_event.wait())

        done, pending = await asyncio.wait(
            [run_task, stop_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()

    except Exception as e:
        logger.error(f"Error running userbot: {e}", exc_info=True)
    finally:
        await userbot.disconnect()
        logger.info("Userbot stopped cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Process terminated.")
