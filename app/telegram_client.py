"""Telethon personal account integration and message handler."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, List, Optional, Tuple
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError

from app.agent import COMMON_NON_NAME_WORDS, RegistrationAgent
from app.config import Config, mask_phone
from app.google_sheets import GoogleSheetsService
from app.registration import (
    ConversationManager,
    RegistrationState,
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
        self._user_msg_history: Dict[int, List[Tuple[float, str]]] = {}

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

            # Anti-Bot Loop protection: Detect automated bot receipts and avoid responding
            lowered_text = text.lower()
            if (
                ("saqlandi" in lowered_text and "✅" in text)
                or "muvaffaqiyatli saqlandi" in lowered_text
                or "muvaffaqiyatli yangilandi" in lowered_text
                or lowered_text.startswith("qabul qilindi!")
                or ("barcha ma'lumotlaringiz" in lowered_text and "saqlandi" in lowered_text)
            ):
                logger.info(
                    f"🤖 [User {sender_id}]: Ignored automated bot receipt to prevent ping-pong loop."
                )
                return

            # Rate limit & duplicate flood protection per user
            now = time.time()
            history = self._user_msg_history.setdefault(sender_id, [])
            history[:] = [(t, msg) for t, msg in history if now - t <= 15.0]

            # Check duplicate message within 10s
            if any(msg == text and now - t <= 10.0 for t, msg in history):
                logger.warning(
                    f"⚠️ [User {sender_id}]: Dropping duplicate message within 10s to prevent loop."
                )
                return

            # Check flood rate limit: max 4 messages in 10s
            recent_count = sum(1 for t, _ in history if now - t <= 10.0)
            if recent_count >= 4:
                logger.warning(
                    f"⚠️ [User {sender_id}]: Rate limit exceeded ({recent_count} msgs in 10s). Dropping to protect API quota."
                )
                return

            history.append((now, text))
            logger.info(f"📩 [User {sender_id}]: {text}")

            # 2. Strict Access Control check: Exclusions and Allowlist
            if not self.config.access_controller.is_allowed(sender_id):
                logger.info(
                    f"⛔ User {sender_id} is excluded or not in allowlist. Ignoring silently."
                )
                return

            # 3. Retrieve conversation session and record user message
            session = self.conversation_manager.get_session(sender_id)
            session.add_message("user", text)

            # 4. Pass to Registration Agent for natural-language analysis
            agent_result = self.agent.process_message(text, session)
            logger.info(
                f"🧠 [Agent {sender_id}]: intent={agent_result.is_registration_intent}, "
                f"confirm={agent_result.is_confirmation}, corr={agent_result.is_correction}"
            )

            # 5. Handle Cancellation
            if agent_result.is_cancellation:
                if session.state != RegistrationState.IDLE:
                    logger.info(f"🛑 User {sender_id} cancelled registration.")
                    session.reset()
                    cancel_msg = (
                        agent_result.custom_reply
                        or (
                            "Tushundim, ro'yxatdan o'tish to'xtatildi. Agar fikringiz o'zgarsa yoki yana yozilishni istasangiz, bemalol murojaat qilishingiz mumkin."
                            if session.language == "uz"
                            else (
                                "Понял, регистрация отменена. Если передумаете, пишите в любое время."
                                if session.language == "ru"
                                else "Registration cancelled. Let me know if you need anything else."
                            )
                        )
                    )
                    await self._safe_respond(event, cancel_msg)
                    session.add_message("assistant", cancel_msg)
                    logger.info(f"📤 [Bot -> {sender_id}]: {cancel_msg}")
                return

            # 6. Handle Confirmation when awaiting confirmation
            if session.state == RegistrationState.AWAITING_CONFIRMATION and not agent_result.is_correction:
                if agent_result.is_confirmation or is_confirmation_intent(text):
                    # Pre-save validation: Ensure valid names before persisting to Google Sheets
                    f_name = (session.first_name or "").strip()
                    l_name = (session.last_name or "").strip()
                    f_lower = f_name.lower()
                    l_lower = l_name.lower()

                    if (
                        not f_name
                        or len(f_name) < 2
                        or f_lower in COMMON_NON_NAME_WORDS
                        or not l_name
                        or len(l_name) < 2
                        or l_lower in COMMON_NON_NAME_WORDS
                        or f_lower == l_lower
                    ):
                        logger.warning(
                            f"⚠️ Rejecting invalid name before Google Sheets save for user {sender_id}: "
                            f"first_name='{f_name}', last_name='{l_name}'."
                        )
                        session.clear_name()
                        session.state = RegistrationState.COLLECTING
                        reask_msg = (
                            "Ism va familiyangizni to'liq ajrata olmadim. "
                            "Iltimos, ism va familiyangizni to'liq yozib yuborsangiz (masalan: Sanjar Dagarov)?"
                            if session.language == "uz"
                            else (
                                "Не удалось корректно определить имя и фамилию. "
                                "Пожалуйста, напишите имя и фамилию заново (например: Санжар Дагаров)?"
                                if session.language == "ru"
                                else "Could not verify your full name. Please provide your first and last name (e.g. Sanjar Dagarov)."
                            )
                        )
                        await self._safe_respond(event, reask_msg)
                        session.add_message("assistant", reask_msg)
                        return

                    logger.info(
                        f"✅ User {sender_id} confirmed registration. Saving to Google Sheets by Telegram User ID..."
                    )
                    success = self.google_sheets.save_registration(
                        telegram_user_id=sender_id,
                        first_name=f_name,
                        last_name=l_name,
                        phone_number=session.phone_number or "",
                    )
                    if success:
                        session.state = RegistrationState.CONFIRMED
                        success_msg = (
                            agent_result.custom_reply
                            or (
                                "🎉 **Tabriklayman, muvaffaqiyatli ro'yxatdan o'tdingiz!**\n\n"
                                "Sizni tadbirimizda mamnuniyat bilan kutamiz. Agar qo'shimcha savollaringiz bo'lsa, bemalol so'rashingiz mumkin."
                                if session.language == "uz"
                                else (
                                    "🎉 **Поздравляем, вы успешно зарегистрированы!**\n\n"
                                    "С нетерпением ждем вас на мероприятии. Если возникнут вопросы, обращайтесь в любое время."
                                    if session.language == "ru"
                                    else "🎉 **Congratulations, your registration is confirmed!**\n\nWe look forward to seeing you at the event. Feel free to reach out if you have any questions."
                                )
                            )
                        )
                        await self._safe_respond(event, success_msg)
                        session.add_message("assistant", success_msg)
                        logger.info(f"📤 [Bot -> {sender_id}]: {success_msg}")
                        session.reset()
                    else:
                        error_msg = (
                            "Ma'lumotlarni saqlashda texnik xatolik yuz berdi. "
                            "Iltimos, birozdan so'ng qayta tasdiqlab ko'ring."
                            if session.language == "uz"
                            else (
                                "Произошла техническая ошибка при сохранении данных. "
                                "Пожалуйста, попробуйте подтвердить чуть позже."
                                if session.language == "ru"
                                else "A technical error occurred while saving. Please try confirming again shortly."
                            )
                        )
                        await self._safe_respond(event, error_msg)
                        session.add_message("assistant", error_msg)
                        logger.info(f"📤 [Bot -> {sender_id}]: {error_msg}")
                    return

            # 7. Handle Correction target clearing
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

            # 8. If user is idle and message has no intent or custom reply, ignore
            if (
                session.state == RegistrationState.IDLE
                and not agent_result.is_registration_intent
                and not agent_result.custom_reply
            ):
                logger.debug(
                    f"Message from {sender_id} does not indicate registration intent. Ignoring."
                )
                return

            # 9. Move from IDLE to COLLECTING and update collected entities
            if session.state == RegistrationState.IDLE:
                session.state = RegistrationState.COLLECTING

            session.update_fields(
                first_name=agent_result.extracted_first_name,
                last_name=agent_result.extracted_last_name,
                phone_number=agent_result.extracted_phone_number,
                language=agent_result.detected_language,
            )

            # 10. Respond according to registration state
            if session.is_complete():
                session.state = RegistrationState.AWAITING_CONFIRMATION
                reply = agent_result.custom_reply or session.get_summary_message()
            else:
                session.state = RegistrationState.COLLECTING
                if agent_result.is_correction:
                    reply = agent_result.custom_reply or session.get_correction_prompt(agent_result.correction_target)
                else:
                    reply = agent_result.custom_reply or session.get_missing_prompt()

            await self._safe_respond(event, reply)
            session.add_message("assistant", reply)
            logger.info(f"📤 [Bot -> {sender_id}]: {reply}")

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
