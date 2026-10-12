"""Telethon personal account integration and message handler."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError

from app.agent import RegistrationAgent
from app.config import Config, mask_phone
from app.google_sheets import GoogleSheetsService
from app.registration import (
    ConversationManager,
    RegistrationState,
    is_cancellation_intent,
    is_confirmation_intent,
)

logger = logging.getLogger(__name__)


class UserbotService:
    """Manages Telethon Telegram client lifecycle and private message handling."""

    def __init__(
        self,
        config: Config,
        conversation_manager: ConversationManager,
        agent: RegistrationAgent,
        google_sheets: GoogleSheetsService,
    ) -> None:
        self.config = config
        self.conversation_manager = conversation_manager
        self.agent = agent
        self.google_sheets = google_sheets
        self.client: Optional[TelegramClient] = None

    async def start(self) -> None:
        """Start the Telethon client and register event handlers."""
        session_file = self.config.tg_session_name
        self.client = TelegramClient(
            session_file,
            self.config.tg_api_id,
            self.config.tg_api_hash,
        )

        self._register_handlers()

        logger.info(
            f"Connecting to Telegram for account phone: {mask_phone(self.config.tg_phone)}..."
        )
        await self.client.start(phone=self.config.tg_phone)
        me = await self.client.get_me()
        logger.info(
            f"Telethon successfully connected as: {getattr(me, 'first_name', '')} "
            f"(@{getattr(me, 'username', 'no_username')}, ID: {me.id})"
        )

    def _register_handlers(self) -> None:
        """Register incoming message event handlers on Telethon client."""
        assert self.client is not None

        @self.client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
        async def on_private_message(event: events.NewMessage.Event) -> None:
            await self._handle_incoming_message(event)

    async def _handle_incoming_message(self, event: events.NewMessage.Event) -> None:
        """Process incoming private messages strictly adhering to registration logic."""
        try:
            # 1. Ignore outgoing messages, non-private messages, or messages without text
            if event.out or not event.is_private:
                return

            text = (event.raw_text or "").strip()
            if not text:
                return

            sender = await event.get_sender()
            if not sender or getattr(sender, "bot", False):
                return

            sender_id = sender.id

            # 2. Strict Access Control check: Exclusions and Allowlist
            if not self.config.access_controller.is_allowed(sender_id):
                logger.debug(
                    f"User {sender_id} is excluded or not in allowlist. Ignoring silently."
                )
                return

            # 3. Retrieve conversation session
            session = self.conversation_manager.get_session(sender_id)

            # 4. Check Cancellation intent across all states
            if is_cancellation_intent(text):
                if session.state != RegistrationState.IDLE:
                    logger.info(f"User {sender_id} cancelled registration.")
                    session.reset()
                    cancel_msg = (
                        "Ro'yxatdan o'tish bekor qilindi."
                        if session.language == "uz"
                        else (
                            "Регистрация отменена."
                            if session.language == "ru"
                            else "Registration has been cancelled."
                        )
                    )
                    await self._safe_respond(event, cancel_msg)
                return

            # 5. Check Confirmation when awaiting confirmation
            if session.state == RegistrationState.AWAITING_CONFIRMATION:
                if is_confirmation_intent(text):
                    logger.info(
                        f"User {sender_id} confirmed registration. Saving to Google Sheets..."
                    )
                    success = self.google_sheets.save_registration(
                        telegram_user_id=sender_id,
                        first_name=session.first_name or "",
                        last_name=session.last_name or "",
                        phone_number=session.phone_number or "",
                    )
                    if success:
                        session.state = RegistrationState.CONFIRMED
                        success_msg = (
                            "Tabriklaymiz! Siz tadbirga muvaffaqiyatli ro'yxatdan o'tdingiz. Rahmat!"
                            if session.language == "uz"
                            else (
                                "Поздравляем! Вы успешно зарегистрировались на мероприятие. Спасибо!"
                                if session.language == "ru"
                                else "Congratulations! You have successfully registered for the event. Thank you!"
                            )
                        )
                        await self._safe_respond(event, success_msg)
                        # Reset session after confirmed completion
                        session.reset()
                    else:
                        error_msg = (
                            "Kechirasiz, ma'lumotlarni saqlashda texnik xatolik yuz berdi. "
                            "Iltimos, birozdan so'ng qayta urinib ko'ring yoki tasdiqlash uchun 'Ha' deb yozing."
                            if session.language == "uz"
                            else (
                                "Извините, произошла техническая ошибка при сохранении данных. "
                                "Пожалуйста, попробуйте позже или напишите 'Да' для подтверждения."
                                if session.language == "ru"
                                else "Sorry, a technical error occurred while saving your registration. "
                                "Please try again shortly or reply 'Yes' to retry."
                            )
                        )
                        await self._safe_respond(event, error_msg)
                    return

            # 6. Pass to Registration Agent for natural-language analysis
            agent_result = self.agent.process_message(text, session)

            # If user wants to correct previously entered info (e.g. "notug'ri ismni to'girlash kerak")
            if agent_result.is_correction:
                target = agent_result.correction_target
                logger.info(f"User {sender_id} requested correction for target: {target}")

                if target == "phone":
                    session.clear_phone()
                elif target == "name":
                    session.clear_name()
                elif target == "all":
                    session.clear_name()
                    session.clear_phone()

                # If user provided replacement data in the same message
                if (
                    agent_result.extracted_first_name
                    or agent_result.extracted_last_name
                    or agent_result.extracted_phone_number
                ):
                    session.update_fields(
                        first_name=agent_result.extracted_first_name,
                        last_name=agent_result.extracted_last_name,
                        phone_number=agent_result.extracted_phone_number,
                        language=agent_result.detected_language,
                    )

                if session.is_complete():
                    session.state = RegistrationState.AWAITING_CONFIRMATION
                    summary_msg = session.get_summary_message()
                    await self._safe_respond(event, summary_msg)
                else:
                    session.state = RegistrationState.COLLECTING
                    prompt = session.get_correction_prompt(target)
                    await self._safe_respond(event, prompt)
                return

            # If user is idle and no registration intent is detected, silently ignore
            if (
                session.state == RegistrationState.IDLE
                and not agent_result.is_registration_intent
            ):
                logger.debug(
                    f"Message from {sender_id} does not indicate registration intent. Ignoring."
                )
                return

            # If registration intent detected from IDLE state, move to COLLECTING
            if session.state == RegistrationState.IDLE:
                session.state = RegistrationState.COLLECTING

            # Update session fields with any extracted information
            session.update_fields(
                first_name=agent_result.extracted_first_name,
                last_name=agent_result.extracted_last_name,
                phone_number=agent_result.extracted_phone_number,
                language=agent_result.detected_language,
            )

            # 7. Check if all required information is collected
            if session.is_complete():
                session.state = RegistrationState.AWAITING_CONFIRMATION
                summary_msg = session.get_summary_message()
                await self._safe_respond(event, summary_msg)
            else:
                session.state = RegistrationState.COLLECTING
                # Use custom reply if provided, or generated missing fields prompt
                reply = agent_result.custom_reply or session.get_missing_prompt()
                await self._safe_respond(event, reply)

        except FloodWaitError as e:
            logger.warning(
                f"Telegram FloodWaitError: waiting {e.seconds} seconds before resuming."
            )
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logger.error(f"Unexpected error handling message from user: {e}", exc_info=True)

    async def _safe_respond(self, event: events.NewMessage.Event, text: str) -> None:
        """Send response message with FloodWait protection."""
        try:
            await event.respond(text)
        except FloodWaitError as e:
            logger.warning(f"Telegram FloodWaitError on respond: waiting {e.seconds}s.")
            await asyncio.sleep(e.seconds)
            await event.respond(text)

    async def run_until_disconnected(self) -> None:
        """Keep the client running until disconnected or stopped."""
        assert self.client is not None
        await self.client.run_until_disconnected()

    async def disconnect(self) -> None:
        """Disconnect the Telethon client gracefully."""
        if self.client and self.client.is_connected():
            logger.info("Disconnecting Telethon client...")
            await self.client.disconnect()
