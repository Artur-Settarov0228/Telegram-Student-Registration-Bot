"""AI Agent and Natural Language Understanding module for participant registration."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional, Set, Tuple

from app.config import Config
from app.registration import (
    RegistrationSession,
    RegistrationState,
    is_cancellation_intent,
    is_confirmation_intent,
    is_correction_intent,
    normalize_and_validate_phone,
)

logger = logging.getLogger(__name__)

# Registration and attendance intent triggers
REGISTRATION_INTENT_PHRASES: Set[str] = {
    # Uzbek
    "ro'yxat", "royxat", "tadbir", "qatnash", "yozil", "ishtirok",
    "boraman", "kelaman", "seminar", "kurs", "vebinar", "joy",
    # Russian
    "регистрация", "зарегистрироваться", "записаться", "участвовать", "приду",
    "мероприятие", "семинар", "вебинар",
    # English
    "register", "registration", "attend", "participate", "join", "event",
}

GREETING_KEYWORDS: Set[str] = {
    "assalomu", "assalom", "alaykum", "salom", "здравствуйте", "привет", "hello", "hi", "hey",
}

# Words that must NEVER be extracted as participant names
COMMON_NON_NAME_WORDS: Set[str] = {
    # Greetings & politeness
    "assalomu", "assalom", "alaykum", "salom", "hayrli", "iltimos", "rahmat",
    "здравствуйте", "привет", "спасибо", "пожалуйста", "hello", "hi", "hey", "please", "thanks",
    # Verbs & intent words
    "ro'yxat", "royxat", "o'tish", "otish", "yozilmoqchiman", "qatnashmoqchiman",
    "boraman", "kelaman", "ertaga", "bugun", "tadbir", "seminar", "kurs",
    "регистрация", "записаться", "участвовать", "register", "attend", "join", "event",
    # Confirmation, negation & correction words
    "ha", "xa", "yo'q", "yoq", "emas", "xato", "noto'g'ri", "notug'ri", "kerak",
    "to'g'ri", "togri", "o'zgartir", "tuzat", "boshqa", "nomerim", "raqamim", "ismim", "familiyam",
    "да", "нет", "не", "ошибка", "неправильно", "верно", "yes", "no", "wrong", "change", "correct",
}


@dataclass
class AgentResult:
    """Result of processing a message through the agent."""

    is_registration_intent: bool
    is_confirmation: bool = False
    is_cancellation: bool = False
    is_correction: bool = False
    correction_target: Optional[str] = None
    extracted_first_name: Optional[str] = None
    extracted_last_name: Optional[str] = None
    extracted_phone_number: Optional[str] = None
    detected_language: str = "uz"
    custom_reply: Optional[str] = None


class RegistrationAgent:
    """Natural conversational assistant for registration intent detection and information extraction."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._openai_client = None

        if config.openai_api_key:
            try:
                from openai import OpenAI

                kwargs = {"api_key": config.openai_api_key}
                if config.openai_base_url:
                    kwargs["base_url"] = config.openai_base_url
                self._openai_client = OpenAI(**kwargs)
                logger.info(
                    f"OpenAI client initialized with model '{config.openai_model}'."
                )
            except Exception as e:
                logger.warning(
                    f"Could not initialize OpenAI client ({e}). Falling back to conversational engine."
                )

    def process_message(
        self,
        text: str,
        session: RegistrationSession,
    ) -> AgentResult:
        """
        Process incoming text message in the context of the user's session history.
        Returns AgentResult indicating intent, extracted data, and natural reply.
        """
        cleaned_text = text.strip()
        lang = self._detect_language(cleaned_text)

        # 1. Primary path: Use LLM with conversational context if client is configured
        if self._openai_client:
            try:
                return self._process_with_ai(cleaned_text, session)
            except Exception as e:
                logger.warning(f"AI parsing failed ({e}). Falling back to deterministic rules.")

        # 2. Deterministic fallback: cancellation intent
        if is_cancellation_intent(cleaned_text):
            return AgentResult(
                is_registration_intent=True,
                is_cancellation=True,
                detected_language=lang,
            )

        # 3. Deterministic fallback: confirmation intent ONLY when awaiting confirmation
        if session.state == RegistrationState.AWAITING_CONFIRMATION:
            if is_confirmation_intent(cleaned_text):
                return AgentResult(
                    is_registration_intent=True,
                    is_confirmation=True,
                    detected_language=session.language,
                )

        # 4. Deterministic fallback: correction intent
        if session.state in (RegistrationState.COLLECTING, RegistrationState.AWAITING_CONFIRMATION):
            is_corr, target = is_correction_intent(cleaned_text)
            if is_corr:
                phone = self._extract_phone_regex(cleaned_text)
                first_name, last_name = self._extract_names(cleaned_text, known_phone=phone)
                return AgentResult(
                    is_registration_intent=True,
                    is_correction=True,
                    correction_target=target,
                    extracted_first_name=first_name,
                    extracted_last_name=last_name,
                    extracted_phone_number=phone,
                    detected_language=lang,
                )

        # 5. Robust offline rule-based extraction
        return self._process_with_rules(cleaned_text, session)

    def _process_with_ai(
        self,
        text: str,
        session: RegistrationSession,
    ) -> AgentResult:
        """Process message using OpenAI/Gemini chat completions with full conversation context."""
        lang = self._detect_language(text)

        system_prompt = (
            "You are a friendly, natural, and respectful conversational assistant chatting on Telegram on behalf of an event organizer in Uzbekistan.\n"
            "Languages supported: Conversational Uzbek (primary), Russian, English.\n\n"
            "CORE BEHAVIOR & TONE:\n"
            "- Speak naturally like an ordinary person having a warm, polite 1-on-1 Telegram conversation.\n"
            "- NEVER sound like an automated bot, customer-service questionnaire, or rigid form.\n"
            "- Do NOT use repetitive greetings, rigid bullet points, or multiple emojis.\n"
            "- Keep replies concise (1-2 sentences), natural, warm, and respectful.\n\n"
            "CRITICAL SKILLS - ADAPT TO DYNAMIC NEW INFORMATION FROM TELEGRAM:\n"
            "1. Dynamic adaptation: If the user sends ANY new information (e.g. mentions their city, university, profession, arrival preference, or asks a question), DIRECTLY ACKNOWLEDGE AND RESPOND TO THAT NEW INFORMATION in your reply, while seamlessly steering the registration forward.\n"
            "2. Questions & FAQ: If the user asks about the event (e.g. 'Tadbir soat nechada?', 'Qayerda bo\\'ladi?', 'Bepulmi?', 'Online qatnashsa bo\\'ladimi?'), answer helpfully (e.g. 'Tadbir bepul, ertaga soat 14:00 da boshlanadi') and naturally ask for their missing registration details.\n"
            "3. Corrections: If the user corrects or changes previously stated details ('Ismimni xato yozding', 'Yo\\'q, familiyam...', 'Raqamim boshqa: +998...'), acknowledge the correction warmly ('Tushundim, yangilab qo\\'ydim') and update the corresponding fields.\n"
            "4. Partial info: If user gives only first name, ask naturally for surname and phone. If only phone, ask for full name.\n"
            "5. Confirmation: When all 3 fields (first_name, last_name, phone_number) are present and state is AWAITING_CONFIRMATION, if the user confirms ('Ha', 'To\\'g\\'ri', 'Tasdiqlayman'), set is_confirmation: true. Do NOT confirm if they are asking a question or correcting something.\n"
            "6. Cancellation: If user clearly wants to cancel ('Bekor qil', 'To\\'xtat', 'Kerak emas'), set is_cancellation: true.\n"
            "7. Registration Intent: Set is_registration_intent to true if the user's message is related to attending, registering, asking questions about the event (time, place, format, cost), or providing any information. Set false ONLY if it is completely irrelevant personal banter.\n"
            "- NEVER invent or hallucinate personal details.\n\n"
            "Current registration status for this user:\n"
            f"Already collected: first_name={session.first_name!r}, last_name={session.last_name!r}, phone_number={session.phone_number!r}.\n"
            f"Current state: {session.state.value}.\n\n"
            "Output JSON format:\n"
            "{\n"
            '  "is_registration_intent": boolean,\n'
            '  "is_confirmation": boolean,\n'
            '  "is_cancellation": boolean,\n'
            '  "is_correction": boolean,\n'
            '  "correction_target": "name" | "phone" | "all" | null,\n'
            '  "first_name": string | null,\n'
            '  "last_name": string | null,\n'
            '  "phone_number": string | null,\n'
            '  "language": "uz" | "ru" | "en",\n'
            '  "reply": string\n'
            "}"
        )

        # Build messages list incorporating conversation history
        messages = [{"role": "system", "content": system_prompt}]
        for turn in session.history[-6:]:
            messages.append({"role": turn["role"], "content": turn["content"]})
        messages.append({"role": "user", "content": text})

        assert self._openai_client is not None
        response = self._openai_client.chat.completions.create(
            model=self.config.openai_model,
            messages=messages,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content or "{}"
        data = json.loads(content)

        extracted_phone = normalize_and_validate_phone(data.get("phone_number"))
        if not extracted_phone:
            extracted_phone = self._extract_phone_regex(text)

        first_name = data.get("first_name")
        last_name = data.get("last_name")

        # Sanity check against non-name words
        if first_name and first_name.lower().strip() in COMMON_NON_NAME_WORDS:
            first_name = None
        if last_name and last_name.lower().strip() in COMMON_NON_NAME_WORDS:
            last_name = None

        raw_intent = bool(data.get("is_registration_intent", False))
        # If the user asked about event, provided info, or has greetings + questions, treat as intent
        lowered = text.lower()
        has_keywords = any(kw in lowered for kw in REGISTRATION_INTENT_PHRASES)
        if not raw_intent and (has_keywords or first_name or extracted_phone):
            raw_intent = True

        return AgentResult(
            is_registration_intent=raw_intent,
            is_confirmation=bool(data.get("is_confirmation", False)),
            is_cancellation=bool(data.get("is_cancellation", False)),
            is_correction=bool(data.get("is_correction", False)),
            correction_target=data.get("correction_target"),
            extracted_first_name=first_name.strip() if first_name else None,
            extracted_last_name=last_name.strip() if last_name else None,
            extracted_phone_number=extracted_phone,
            detected_language=data.get("language", lang) or "uz",
            custom_reply=data.get("reply"),
        )

    def _process_with_rules(
        self,
        text: str,
        session: RegistrationSession,
    ) -> AgentResult:
        """Conversational natural language parser for offline / rule-based fallback."""
        lang = self._detect_language(text)
        lowered = text.lower()

        # Check phone number
        phone = self._extract_phone_regex(text)

        # Extract names with strict non-name word filtering
        first_name, last_name = self._extract_names(text, known_phone=phone)

        # Check intent: phrases or greetings
        has_intent_phrase = any(phrase in lowered for phrase in REGISTRATION_INTENT_PHRASES)
        has_greeting = any(kw in lowered for kw in GREETING_KEYWORDS)

        # If user is in active registration state, intent is already established
        if session.state in (RegistrationState.COLLECTING, RegistrationState.AWAITING_CONFIRMATION):
            return AgentResult(
                is_registration_intent=True,
                extracted_first_name=first_name,
                extracted_last_name=last_name,
                extracted_phone_number=phone,
                detected_language=lang,
            )

        # If IDLE and user expressed attendance desire, greeting, or gave info
        if has_intent_phrase or has_greeting or (phone and (first_name or last_name)):
            return AgentResult(
                is_registration_intent=True,
                extracted_first_name=first_name,
                extracted_last_name=last_name,
                extracted_phone_number=phone,
                detected_language=lang,
            )

        # Otherwise, unrelated message
        return AgentResult(
            is_registration_intent=False,
            detected_language=lang,
        )

    def _extract_phone_regex(self, text: str) -> Optional[str]:
        """Extract and validate phone number using regular expressions."""
        pattern = r"(\+?[0-9][0-9\s\-\(\)\.]{7,16}[0-9])"
        matches = re.findall(pattern, text)
        for match in matches:
            normalized = normalize_and_validate_phone(match)
            if normalized:
                return normalized
        return None

    def _extract_names(
        self,
        text: str,
        known_phone: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Extract valid first name and last name.
        Strictly ignores greetings, typos, intent keywords, pronouns, and negation words.
        """
        cleaned = text
        if known_phone:
            cleaned = cleaned.replace(known_phone, " ")
        cleaned = re.sub(r"\+?[0-9][0-9\s\-\(\)\.]{7,16}[0-9]", " ", cleaned)

        # Retain letters, spaces, and Uzbek apostrophes
        cleaned = re.sub(r"[^\w\s'ʻ’`\-]", " ", cleaned)

        valid_tokens = []
        for raw_word in cleaned.split():
            norm_word = re.sub(r"[ʻ’`]", "'", raw_word.lower().strip())
            norm_word = re.sub(r"^'+|'+$", "", norm_word)

            if len(norm_word) < 2 or norm_word.isdigit():
                continue
            if norm_word in COMMON_NON_NAME_WORDS:
                continue

            # Check if word is a derivative of metadata words
            if any(
                norm_word.startswith(stem) and len(norm_word) - len(stem) <= 3
                for stem in ["ism", "familiy", "nomer", "raqam", "telefon", "xato", "noto'g'r", "notug'r"]
            ):
                continue

            cleaned_token = re.sub(r"[^a-zA-Zа-яА-ЯёЁo'g'shchO'G'SHCH\s'\-]", "", raw_word).strip("'-`")
            if len(cleaned_token) >= 2:
                valid_tokens.append(cleaned_token.capitalize())

        if len(valid_tokens) == 1:
            return valid_tokens[0], None
        elif len(valid_tokens) >= 2:
            return valid_tokens[0], valid_tokens[1]
        return None, None

    def _detect_language(self, text: str) -> str:
        """Heuristic language detection: Uzbek, Russian, or English."""
        if re.search(r"[а-яА-ЯёЁ]", text):
            return "ru"
        if re.search(r"(o'|g'|sh|ch|moqchiman|kerak|rahmat|qilish|emas|notug|togri|boraman|yozil|tadbir)", text, re.IGNORECASE):
            return "uz"
        if re.search(r"\b(hello|hi|please|name|phone|event|register|confirm|wrong|fix|tomorrow)\b", text, re.IGNORECASE):
            return "en"
        return "uz"
