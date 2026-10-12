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


# Common Uzbek, Russian, English confirmation and cancellation keywords
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
    "bekor", "bekor qilish", "bekor qil", "yo'q", "yoq", "kerakmas", "to'xtat", "to'xtatish",
    # Russian
    "отмена", "отменить", "нет", "не надо", "стоп",
    # English
    "cancel", "no", "stop", "abort",
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

    # Remove formatting characters: spaces, dashes, parentheses, dots
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
    if cleaned in CONFIRMATION_KEYWORDS:
        return True
    words = cleaned.split()
    return any(w in CONFIRMATION_KEYWORDS for w in words if len(w) > 1)


def is_cancellation_intent(text: str) -> bool:
    """Check if message represents a clear cancellation."""
    cleaned = re.sub(r"[^\w\s/']", "", text.lower().strip())
    if cleaned in CANCELLATION_KEYWORDS:
        return True
    words = cleaned.split()
    return any(w in CANCELLATION_KEYWORDS for w in words)


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


def is_correction_intent(text: str) -> tuple[bool, Optional[str]]:
    """
    Check if the user is asking to correct or edit previously entered info.
    Returns (True, target_field) where target_field can be 'name', 'phone', 'all', or None.
    """
    cleaned = text.lower().strip()
    words = set(re.findall(r"[\w']+", cleaned))
    
    is_correcting = any(kw in words or kw in cleaned for kw in CORRECTION_KEYWORDS)
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
    """Isolated registration state for a single user."""

    user_id: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone_number: Optional[str] = None
    state: RegistrationState = RegistrationState.IDLE
    language: str = "uz"
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def is_complete(self) -> bool:
        """Check if all three required fields are present."""
        return bool(self.first_name and self.last_name and self.phone_number)

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
        if phone_number and phone_number.strip():
            normalized = normalize_and_validate_phone(phone_number)
            if normalized:
                self.phone_number = normalized
        if language and language.strip():
            self.language = language.strip().lower()
        self.updated_at = datetime.utcnow()

    def get_summary_message(self) -> str:
        """Generate confirmation message with collected participant details."""
        if self.language == "ru":
            return (
                "Ваши данные для регистрации:\n\n"
                f"• Имя: {self.first_name}\n"
                f"• Фамилия: {self.last_name}\n"
                f"• Телефон: {self.phone_number}\n\n"
                "Все верно? Для подтверждения напишите 'Да'.\n"
                "Для отмены напишите /cancel."
            )
        elif self.language == "en":
            return (
                "Registration details:\n\n"
                f"• First name: {self.first_name}\n"
                f"• Last name: {self.last_name}\n"
                f"• Phone: {self.phone_number}\n\n"
                "Is this correct? Reply 'Yes' to confirm.\n"
                "To cancel, reply /cancel."
            )
        else:
            return (
                "Ro'yxatdan o'tish ma'lumotlaringiz:\n\n"
                f"• Ism: {self.first_name}\n"
                f"• Familiya: {self.last_name}\n"
                f"• Telefon: {self.phone_number}\n\n"
                "Barchasi to'g'rimi? Tasdiqlash uchun 'Ha' deb yozing.\n"
                "Bekor qilish uchun /cancel deb yozing."
            )

    def get_missing_prompt(self) -> str:
        """Generate professional prompt for missing fields."""
        missing = self.missing_fields()
        
        if self.language == "ru":
            if "first_name" in missing and "last_name" in missing and "phone_number" in missing:
                return "Здравствуйте! Для регистрации на мероприятие отправьте ваше имя, фамилию и номер телефона."
            if "first_name" in missing and "last_name" in missing:
                return "Пожалуйста, отправьте ваше имя и фамилию."
            if "first_name" in missing:
                return "Пожалуйста, укажите ваше имя."
            if "last_name" in missing:
                return "Пожалуйста, укажите вашу фамилию."
            if "phone_number" in missing:
                return "Пожалуйста, отправьте ваш номер телефона (например: +998901234567)."
            return "Пожалуйста, отправьте недостающие данные."

        elif self.language == "en":
            if "first_name" in missing and "last_name" in missing and "phone_number" in missing:
                return "Hello! To register for the event, please provide your first name, surname, and phone number."
            if "first_name" in missing and "last_name" in missing:
                return "Please provide your first name and surname."
            if "first_name" in missing:
                return "Please provide your first name."
            if "last_name" in missing:
                return "Please provide your surname."
            if "phone_number" in missing:
                return "Please provide your phone number (e.g. +998901234567)."
            return "Please provide the missing details."

        else:  # Default Uzbek
            if "first_name" in missing and "last_name" in missing and "phone_number" in missing:
                return "Assalomu alaykum! Tadbirga ro'yxatdan o'tish uchun ismingiz, familiyangiz va telefon raqamingizni yuboring."
            if "first_name" in missing and "last_name" in missing:
                return "Iltimos, ismingiz va familiyangizni yuboring."
            if "first_name" in missing:
                return "Iltimos, ismingizni yuboring."
            if "last_name" in missing:
                return "Iltimos, familiyangizni yuboring."
            if "phone_number" in missing:
                return "Iltimos, telefon raqamingizni yuboring (masalan: +998901234567)."
            return "Iltimos, yetishmayotgan ma'lumotlarni yuboring."

    def clear_name(self) -> None:
        """Clear first and last name for correction."""
        self.first_name = None
        self.last_name = None
        self.updated_at = datetime.utcnow()

    def clear_phone(self) -> None:
        """Clear phone number for correction."""
        self.phone_number = None
        self.updated_at = datetime.utcnow()

    def get_correction_prompt(self, target: Optional[str] = None) -> str:
        """Get concise, professional prompt when user requests a correction."""
        if self.language == "ru":
            if target == "phone":
                return "Пожалуйста, отправьте правильный номер телефона (например: +998901234567)."
            elif target == "name":
                return "Пожалуйста, отправьте правильное имя и фамилию (например: Али Валиев)."
            else:
                return "Пожалуйста, отправьте правильные данные."
        elif self.language == "en":
            if target == "phone":
                return "Please send your correct phone number (e.g. +998901234567)."
            elif target == "name":
                return "Please send your correct first name and surname (e.g. Ali Valiyev)."
            else:
                return "Please send your correct details."
        else:
            if target == "phone":
                return "Iltimos, to'g'ri telefon raqamingizni yuboring (masalan: +998901234567)."
            elif target == "name":
                return "Iltimos, to'g'ri ism va familiyangizni yuboring (masalan: Ali Valiyev)."
            else:
                return "Iltimos, to'g'ri ma'lumotlarni yuboring."

    def reset(self) -> None:
        """Reset session to idle state."""
        self.first_name = None
        self.last_name = None
        self.phone_number = None
        self.state = RegistrationState.IDLE
        self.language = "uz"
        self.updated_at = datetime.utcnow()


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
