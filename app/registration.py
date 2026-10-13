"""Deterministic conversation-state manager for participant registration."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional


class RegistrationState(str, Enum):
    """Conversation state enumeration."""

    IDLE = "IDLE"
    COLLECTING = "COLLECTING"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


# Common confirmation keywords and phrases
CONFIRMATION_KEYWORDS = {
    # Uzbek
    "ha", "xa", "tasdiqlayman", "to'g'ri", "togri", "albatta", "mayli", "shunday",
    # Russian
    "да", "подтверждаю", "верно", "правильно", "согласен", "согласна",
    # English
    "yes", "confirm", "confirmed", "correct", "yep", "sure", "ok", "okay",
}

CANCELLATION_KEYWORDS = {
    # Command
    "/cancel",
    # Uzbek
    "bekor", "bekor qilish", "bekor qil", "kerakmas", "kerak emas", "to'xtat", "to'xtatish", "qatnashmayman", "bormayman",
    # Russian
    "отмена", "отменить", "не надо", "стоп", "не буду",
    # English
    "cancel", "stop", "abort",
}

CORRECTION_KEYWORDS = {
    # Uzbek
    "noto'g'ri", "notug'ri", "notugri", "noto'gri", "xato", "xatolik",
    "to'g'rilash", "to'girlash", "togirlash", "o'zgartirish", "ozgartirish",
    "almashtirish", "almashtir", "tuzatish", "tuzat", "boshqattan", "boshidan",
    # Russian
    "неправильно", "ошибка", "исправить", "поменять", "изменить", "заново",
    # English
    "wrong", "incorrect", "error", "fix", "change", "edit", "reset",
}


def normalize_and_validate_phone(phone_raw: str | None) -> str | None:
    """
    Validate and normalize phone number into standard E.164-like format.
    Supports Uzbek formats (+998901234567, 998901234567, 901234567)
    and valid international formats (+...).
    Returns normalized string like +998901234567, or None if invalid.
    """
    if not phone_raw:
        return None

    cleaned = re.sub(r"[\s\-\(\)\.]", "", phone_raw.strip())
    if not cleaned:
        return None

    # Uzbek standard 9 digits without country code (e.g. 901234567)
    if re.fullmatch(r"[0-9]{9}", cleaned):
        return f"+998{cleaned}"

    # Starts with 998 and 9 digits (12 digits total)
    if re.fullmatch(r"998[0-9]{9}", cleaned):
        return f"+{cleaned}"

    # Standard international with '+' and 9 to 15 digits
    if re.fullmatch(r"\+[0-9]{9,15}", cleaned):
        return cleaned

    # Pure digits without '+', between 10 and 15 digits
    if re.fullmatch(r"[0-9]{10,15}", cleaned):
        return f"+{cleaned}"

    return None


def is_confirmation_intent(text: str) -> bool:
    """Check if message represents a clear confirmation."""
    cleaned = re.sub(r"[^\w\s/']", "", text.lower().strip())

    # If message contains negation, hesitation or correction indicators, it is NOT confirmation
    negation_or_correction = [
        "lekin", "ammo", "boshqa", "noto'g'ri", "notug'ri", "xato", "emas",
        "o'zgartir", "tuzat", "но", "неправильно", "ошибка", "другой", "but", "wrong", "change"
    ]
    if any(neg in cleaned for neg in negation_or_correction):
        return False
    
    # Check natural multi-word confirmations
    confirmation_phrases = [
        "ha shu to'g'ri", "ha shu togri", "shu to'g'ri", "shu togri",
        "ha to'g'ri", "ha togri", "ha shunday", "to'g'ri", "togri",
        "tasdiqlayman", "albatta", "да верно", "все верно", "подтверждаю",
        "yes confirm", "that's correct", "thats correct"
    ]
    if any(phrase in cleaned for phrase in confirmation_phrases):
        return True

    if cleaned in CONFIRMATION_KEYWORDS:
        return True

    words = cleaned.split()
    return any(w in CONFIRMATION_KEYWORDS for w in words if len(w) > 1 or w in {"ha", "xa", "да"})


def is_cancellation_intent(text: str) -> bool:
    """Check if message represents a clear cancellation."""
    cleaned = re.sub(r"[^\w\s/']", "", text.lower().strip())
    if cleaned in CANCELLATION_KEYWORDS:
        return True
    words = cleaned.split()
    return any(w in CANCELLATION_KEYWORDS for w in words)


def is_correction_intent(text: str) -> tuple[bool, Optional[str]]:
    """
    Check if the user is asking to correct or edit previously entered info.
    Returns (True, target_field) where target_field can be 'name', 'phone', 'all', or None.
    """
    cleaned = text.lower().strip()
    words = set(re.findall(r"[\w']+", cleaned))

    # Detect correction intent from phrases or keywords
    phrases = [
        "ismimni xato yozding", "ism xato", "ismim xato", "boshqa raqam",
        "boshqa nomer", "boshqa ism", "xato yozding", "noto'g'ri", "notug'ri",
        "неправильно", "ошибка", "другой номер", "wrong name", "wrong number"
    ]
    is_correcting = any(p in cleaned for p in phrases) or any(kw in words for kw in CORRECTION_KEYWORDS)
    if not is_correcting:
        return False, None

    # Detect target field
    if any(k in cleaned for k in ["ism", "familiya", "name", "имя", "фамили"]):
        return True, "name"
    if any(k in cleaned for k in ["telefon", "nomer", "raqam", "phone", "номер"]):
        return True, "phone"
    if any(k in cleaned for k in ["hammasi", "boshidan", "boshqattan", "все", "заново", "all"]):
        return True, "all"

    return True, None


@dataclass
class RegistrationSession:
    """Isolated registration state and conversation history for a single user."""

    user_id: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone_number: Optional[str] = None
    state: RegistrationState = RegistrationState.IDLE
    language: str = "uz"
    updated_at: datetime = field(default_factory=datetime.utcnow)
    history: List[Dict[str, str]] = field(default_factory=list)
    is_already_registered: bool = False

    def add_message(self, role: str, content: str) -> None:
        """Record turn in history, retaining recent conversation context."""
        self.history.append({"role": role, "content": content})
        if len(self.history) > 10:
            self.history = self.history[-10:]

    def is_complete(self) -> bool:
        """Check if all three required fields are present and distinct."""
        if not (self.first_name and self.last_name and self.phone_number):
            return False
        if self.first_name.strip().lower() == self.last_name.strip().lower():
            return False
        return True

    def missing_fields(self) -> List[str]:
        """Return list of missing fields."""
        missing = []
        if not self.first_name:
            missing.append("first_name")
        if not self.last_name:
            missing.append("last_name")
        if not self.phone_number:
            missing.append("phone_number")
        return missing

    def update_fields(
        self,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        phone_number: Optional[str] = None,
        language: Optional[str] = None,
    ) -> None:
        """Update fields only if valid values provided."""
        if first_name and first_name.strip():
            self.first_name = first_name.strip()
        if last_name and last_name.strip():
            self.last_name = last_name.strip()
        if self.first_name and self.last_name and self.first_name.lower() == self.last_name.lower():
            self.last_name = None
        if phone_number and phone_number.strip():
            normalized = normalize_and_validate_phone(phone_number)
            if normalized:
                self.phone_number = normalized
        if language and language.strip():
            self.language = language.strip().lower()
        self.updated_at = datetime.utcnow()

    def clear_name(self) -> None:
        """Clear first and last name for correction."""
        self.first_name = None
        self.last_name = None
        self.updated_at = datetime.utcnow()

    def clear_phone(self) -> None:
        """Clear phone number for correction."""
        self.phone_number = None
        self.updated_at = datetime.utcnow()

    def formatted_phone(self) -> str:
        """Format phone number for clean aesthetic display (e.g. +998 90 123 45 67)."""
        if not self.phone_number:
            return ""
        p = self.phone_number
        if p.startswith("+998") and len(p) == 13:
            return f"+998 {p[4:6]} {p[6:9]} {p[9:11]} {p[11:13]}"
        return p

    def get_summary_message(self) -> str:
        """Aesthetic confirmation prompt formatted cleanly with Markdown."""
        phone = self.formatted_phone()
        if self.language == "ru":
            return (
                "Отлично, ваши данные подготовлены:\n\n"
                f"👤 **Имя и фамилия:** {self.first_name} {self.last_name}\n"
                f"📞 **Номер телефона:** {phone}\n\n"
                "Все данные верны? Подтверждаете регистрацию?"
            )
        elif self.language == "en":
            return (
                "Great, here are your registration details:\n\n"
                f"👤 **Name:** {self.first_name} {self.last_name}\n"
                f"📞 **Phone number:** {phone}\n\n"
                "Is all information correct? Please confirm."
            )
        else:
            return (
                "Ajoyib, ma'lumotlaringizni tayyorladim:\n\n"
                f"👤 **Ism-familiya:** {self.first_name} {self.last_name}\n"
                f"📞 **Telefon raqam:** {phone}\n\n"
                "Barcha ma'lumotlar to'g'rimi, tasdiqlaysizmi?"
            )

    def get_missing_prompt(self) -> str:
        """Conversational, natural prompt asking for missing details."""
        missing = self.missing_fields()
        
        if self.language == "ru":
            if len(missing) == 3:
                return "Здравствуйте! Чтобы записаться на мероприятие, напишите ваше имя, фамилию и номер телефона."
            if "first_name" in missing and "last_name" in missing:
                return "Понял. Напишите, пожалуйста, ваше имя и фамилию."
            if "phone_number" in missing:
                name_ref = f"{self.first_name}, " if self.first_name else ""
                return f"{name_ref}номер телефона тоже отправьте, пожалуйста."
            if "last_name" in missing:
                return f"{self.first_name}, фамилию тоже напишите, пожалуйста."
            return "Не хватает некоторых данных, отправьте их, пожалуйста."

        elif self.language == "en":
            if len(missing) == 3:
                return "Hello! To register for the event, please share your first name, surname, and phone number."
            if "phone_number" in missing:
                name_ref = f"{self.first_name}, " if self.first_name else ""
                return f"{name_ref}could you also send your phone number?"
            return "Please provide the missing details."

        else:  # Default Uzbek
            if len(missing) == 3:
                return "Assalomu alaykum! Tadbirga ro'yxatdan o'tishingiz uchun ism-familiyangiz va telefon raqamingizni yozib yubora olasizmi?"
            if "first_name" in missing and "last_name" in missing:
                return "Tushundim. Sizni ro'yxatga kiritib qo'yishimiz uchun ism va familiyangizni ham yozib yuborsangiz?"
            if "phone_number" in missing:
                name_ref = f"{self.first_name}, " if self.first_name else ""
                return f"{name_ref}siz bilan bog'lanishimiz uchun telefon raqamingizni ham yozib yubora olasizmi?"
            if "last_name" in missing:
                return f"{self.first_name}, familiyangizni ham yozib yuborsangiz?"
            return "Ro'yxatdan o'tishni yakunlash uchun yetishmayotgan ma'lumotlarni ham yozib yuborsangiz?"

    def get_correction_prompt(self, target: Optional[str] = None) -> str:
        """Conversational acknowledgement when user requests a correction."""
        if self.language == "ru":
            if target == "phone":
                return "Понял, исправим номер. Отправьте правильный номер телефона."
            elif target == "name":
                return "Понял, исправим имя. Напишите правильное имя и фамилию."
            else:
                return "Хорошо, исправим. Напишите правильные данные."
        elif self.language == "en":
            if target == "phone":
                return "Got it, let's update your number. Please send the correct phone number."
            elif target == "name":
                return "Understood, let's fix your name. Please send your correct name and surname."
            else:
                return "Sure, let's update that. Please send the correct info."
        else:
            if target == "phone":
                return "Tushundim, telefon raqamingizni yangilaymiz. To'g'ri raqamingizni yozib yuboring."
            elif target == "name":
                return "Tushunarli, ismingizni to'g'rilaymiz. Iltimos, to'g'ri ism-familiyangizni yozib yuborsangiz?"
            else:
                return "Mayli, ma'lumotlaringizni to'g'rilab qo'yamiz. To'g'ri ma'lumotlarni yozib yuboring."

    def reset(self) -> None:
        """Reset session to idle state."""
        self.first_name = None
        self.last_name = None
        self.phone_number = None
        self.state = RegistrationState.IDLE
        self.language = "uz"
        self.updated_at = datetime.utcnow()
        # Keep recent history for context continuity


class ConversationManager:
    """Thread-safe conversation state tracker for all participants."""

    def __init__(self) -> None:
        self._sessions: Dict[int, RegistrationSession] = {}

    def get_session(self, user_id: int) -> RegistrationSession:
        """Get or initialize isolated session for user_id."""
        if user_id not in self._sessions:
            self._sessions[user_id] = RegistrationSession(user_id=user_id)
        return self._sessions[user_id]

    def clear_session(self, user_id: int) -> None:
        """Remove user session completely."""
        self._sessions.pop(user_id, None)

    def reset_session(self, user_id: int) -> RegistrationSession:
        """Reset user session back to IDLE state."""
        session = self.get_session(user_id)
        session.reset()
        return session
