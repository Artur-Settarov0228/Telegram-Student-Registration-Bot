"""AI Agent and Natural Language Understanding module for participant registration."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional, Set, Tuple

from app.config import Config, mask_phone
from app.registration import (
    RegistrationSession,
    RegistrationState,
    is_cancellation_intent,
    is_confirmation_intent,
    is_correction_intent,
    normalize_and_validate_phone,
)

logger = logging.getLogger(__name__)

# Registration keywords for intent detection in Uzbek, Russian, English
REGISTRATION_KEYWORDS: Set[str] = {
    # Uzbek
    "ro'yxat", "royxat", "ro'yxatdan", "royxatdan", "tadbir", "tadbirga",
    "qatnash", "qatnashmoqchiman", "qatnashmoqchi", "yozil", "yozilmoqchiman",
    "yozilmoqchi", "ishtirok", "ishtirokchi", "seminar", "konferensiya",
    "vebinar", "kurs", "qabul",
    # Russian
    "регистрация", "зарегистрироваться", "записаться", "участвовать",
    "мероприятие", "семинар", "вебинар", "конференция",
    # English
    "register", "registration", "attend", "participate", "join", "event",
    "webinar", "seminar", "conference",
}

# Words that must NEVER be treated as participant names
COMMON_NON_NAME_WORDS: Set[str] = {
    # Uzbek greetings & variants
    "assalomu", "assalomy", "assalom", "assalamu", "alaykum", "aleykum", "alekum",
    "va", "valaykum", "salom", "salomlar", "hayrli", "tong", "kun", "oqshom", "kech",
    # Uzbek registration verbs & intent words
    "ro'yxat", "royxat", "ro'yxatdan", "royxatdan", "o'tish", "otish", "o'tmoqchiman",
    "otmoqchiman", "o'tmoqchi", "otmoqchi", "yozilmoqchiman", "yozilmoqchi", "yozilish",
    "qatnashmoqchiman", "qatnashmoqchi", "qatnashish", "ishtirok", "etmoqchiman", "etish",
    "tadbir", "tadbirga", "seminar", "seminarga", "konferensiya", "vebinar", "kurs", "kursga",
    "bormoqchiman", "bormoqchi", "kelmoqchiman", "qabul",
    # Uzbek corrections, negations & feedback words
    "noto'g'ri", "notug'ri", "notugri", "noto'gri", "xato", "xatolik", "yo'q", "yoq", "emas",
    "kerak", "to'g'rilash", "to'girlash", "togirlash", "to'g'ri", "togri", "o'zgartirish",
    "ozgartirish", "o'zgartir", "almashtirish", "almashtir", "tuzatish", "tuzat", "boshqattan",
    "boshidan", "qayta", "yana", "qara", "bu", "shuni", "huddi", "odamday", "javob", "berish",
    # Uzbek pronouns & metadata nouns
    "ism", "ismim", "ismi", "ismni", "familiya", "familiyam", "familiyasi", "familiyani",
    "nomer", "nomerim", "nomerini", "raqam", "raqamim", "raqamini", "telefon", "telefonim",
    "telefonni", "men", "mening", "man", "maning", "menga", "manga", "iltimos", "rahmat",
    "ha", "xa", "ok", "mayli", "shunday",
    # Russian stopwords
    "привет", "здравствуйте", "здрасте", "добрый", "день", "утро", "вечер",
    "регистрация", "зарегистрироваться", "записаться", "участвовать", "хочу", "мероприятие",
    "семинар", "вебинар", "конференция", "неправильно", "ошибка", "исправить", "поменять",
    "изменить", "заново", "нет", "не", "имя", "фамилия", "номер", "телефон", "меня", "зовут",
    "пожалуйста", "спасибо", "да", "верно", "правильно",
    # English stopwords
    "hello", "hi", "hey", "good", "morning", "afternoon", "evening",
    "register", "registration", "attend", "join", "event", "webinar", "seminar", "conference",
    "wrong", "incorrect", "error", "fix", "change", "edit", "reset", "no", "not", "name",
    "phone", "please", "thank", "thanks", "yes", "confirm", "ok", "sure",
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
    """Natural language agent for registration intent detection and information extraction."""

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
                    f"Could not initialize OpenAI client ({e}). Falling back to rule-based engine."
                )

    def process_message(
        self,
        text: str,
        session: RegistrationSession,
    ) -> AgentResult:
        """
        Process incoming text message in the context of the user's session.
        Returns AgentResult indicating intent, extracted data, and suggested reply.
        """
        cleaned_text = text.strip()
        lang = self._detect_language(cleaned_text)

        # 1. Deterministic check: cancellation intent
        if is_cancellation_intent(cleaned_text):
            return AgentResult(
                is_registration_intent=True,
                is_cancellation=True,
                detected_language=lang,
            )

        # 2. Deterministic check: confirmation intent when awaiting confirmation
        if session.state == RegistrationState.AWAITING_CONFIRMATION:
            if is_confirmation_intent(cleaned_text):
                return AgentResult(
                    is_registration_intent=True,
                    is_confirmation=True,
                    detected_language=session.language,
                )

        # 3. Deterministic check: correction intent when active
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

        # 4. If OpenAI client is available, use LLM for natural language understanding
        if self._openai_client:
            try:
                return self._process_with_ai(cleaned_text, session)
            except Exception as e:
                logger.warning(f"AI parsing failed ({e}). Using rule-based fallback.")

        # 5. Fallback: robust rule-based natural language parser
        return self._process_with_rules(cleaned_text, session)

    def _process_with_ai(
        self,
        text: str,
        session: RegistrationSession,
    ) -> AgentResult:
        """Process message using OpenAI chat completions."""
        lang = self._detect_language(text)

        system_prompt = (
            "You are an intelligent, polite personal assistant managing event registrations on Telegram.\n"
            "Languages supported: Uzbek (primary), Russian, English.\n"
            "Event registration requires exactly: first_name, last_name, and phone_number.\n\n"
            "Instructions:\n"
            "1. 'is_registration_intent': true if the user wants to register, attend an event, or is continuing registration. FALSE if message is unrelated chit-chat, spam, or casual greeting with no registration desire.\n"
            "2. 'is_correction': true if the user states that previous details are wrong or needs to edit (e.g. 'notug\\'ri ismni to\\'girlash kerak', 'ismim xato', 'неправильно', 'wrong name').\n"
            "3. 'correction_target': 'name', 'phone', or 'all' if user is correcting.\n"
            "4. 'first_name': REAL participant first name only. NEVER treat greetings ('Assalomu', 'Salom'), words like 'Notug\\'ri', or intent words as names!\n"
            "5. 'last_name': REAL participant last name only. NEVER treat 'Alaykum', 'Ismni', etc. as names!\n"
            "6. 'phone_number': Participant's phone number if clearly stated, or null.\n"
            "7. 'is_confirmation': true if user clearly confirms details (e.g. 'Ha', 'Tasdiqlayman', 'Да', 'Yes').\n"
            "8. 'is_cancellation': true if user wants to cancel ('/cancel', 'Bekor qilish').\n"
            "9. 'reply': A friendly, human response in the detected language.\n"
            "   - If user just greeted and asked to register (e.g. 'assalomu alaykum ro\\'yxat'): 'Assalomu alaykum! Tadbirga ro\\'yxatdan o\\'tish uchun ismingiz, familiyangiz va telefon raqamingiz kerak bo\\'ladi.'\n"
            "   - If user asked to fix name: 'Kechirasiz! Iltimos, to\\'g\\'ri ism va familiyangizni yuboring (masalan: Ali Valiyev).'\n\n"
            "Respond ONLY in valid JSON format:\n"
            "{\n"
            '  "is_registration_intent": boolean,\n'
            '  "is_correction": boolean,\n'
            '  "correction_target": "name" | "phone" | "all" | null,\n'
            '  "first_name": string | null,\n'
            '  "last_name": string | null,\n'
            '  "phone_number": string | null,\n'
            '  "is_confirmation": boolean,\n'
            '  "is_cancellation": boolean,\n'
            '  "language": "uz" | "ru" | "en",\n'
            '  "reply": string | null\n'
            "}"
        )

        user_context = {
            "current_state": session.state.value,
            "already_collected": {
                "first_name": session.first_name,
                "last_name": session.last_name,
                "phone_number": session.phone_number,
            },
            "user_message": text,
        }

        assert self._openai_client is not None
        response = self._openai_client.chat.completions.create(
            model=self.config.openai_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_context, ensure_ascii=False)},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content or "{}"
        data = json.loads(content)

        extracted_phone = normalize_and_validate_phone(data.get("phone_number"))
        if not extracted_phone:
            extracted_phone = self._extract_phone_regex(text)

        first_name = data.get("first_name")
        last_name = data.get("last_name")

        # Sanity check: discard if extracted name is in stop words
        if first_name and first_name.lower().strip() in COMMON_NON_NAME_WORDS:
            first_name = None
        if last_name and last_name.lower().strip() in COMMON_NON_NAME_WORDS:
            last_name = None

        return AgentResult(
            is_registration_intent=bool(data.get("is_registration_intent", False)),
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
        """Deterministic rule-based intent and entity extraction."""
        lang = self._detect_language(text)
        lowered = text.lower()

        # Check phone number
        phone = self._extract_phone_regex(text)

        # Extract names with strict stopword filtering
        first_name, last_name = self._extract_names(text, known_phone=phone)

        # If user is in active registration state (COLLECTING or AWAITING_CONFIRMATION)
        if session.state in (RegistrationState.COLLECTING, RegistrationState.AWAITING_CONFIRMATION):
            return AgentResult(
                is_registration_intent=True,
                extracted_first_name=first_name,
                extracted_last_name=last_name,
                extracted_phone_number=phone,
                detected_language=lang,
            )

        # If session is IDLE, check for greetings, registration keywords, or name/phone
        GREETING_KEYWORDS = {
            "assalomu", "assalomy", "assalom", "assalamu", "alaykum", "alykum",
            "alekum", "aleykum", "salom", "salomlar", "здравствуйте", "привет", "hello", "hi"
        }
        has_greeting = any(kw in lowered for kw in GREETING_KEYWORDS)
        has_reg_keyword = any(kw in lowered for kw in REGISTRATION_KEYWORDS)

        # If user sent greeting or registration intent or provided details
        if has_reg_keyword or has_greeting or (phone and (first_name or last_name)):
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

            # Check if word is a derivative of meta words (e.g. ismimni, familiyasini, nomeriga)
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
        if re.search(r"(o'|g'|sh|ch|moqchiman|kerak|rahmat|qilish|emas|notug|togri|qara)", text, re.IGNORECASE):
            return "uz"
        if re.search(r"\b(hello|hi|please|name|phone|event|register|confirm|wrong|fix)\b", text, re.IGNORECASE):
            return "en"
        return "uz"
