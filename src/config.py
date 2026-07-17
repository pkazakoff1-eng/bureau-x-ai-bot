"""BX Assistant — конфигурация. Все настройки через .env."""
import os
from dotenv import load_dotenv

load_dotenv()

# ── API keys ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_KEY  = os.environ["ANTHROPIC_KEY"]
TAVILY_KEY     = os.environ["TAVILY_KEY"]
WAVESPEED_KEY  = os.environ["WAVESPEED_KEY"]

# ── Владелец и VIP ────────────────────────────────────────────────────────────
OWNER_ID = int(os.getenv("OWNER_ID", "285198612"))
VIP_ID   = int(os.getenv("VIP_ID", "587290278"))

DB_PATH = os.getenv("DB_PATH", "memory.db")

# ── Модели ────────────────────────────────────────────────────────────────────
CHAT_MODEL = os.getenv("CHAT_MODEL", "claude-sonnet-4-6")
UTIL_MODEL = os.getenv("UTIL_MODEL", "claude-haiku-4-5-20251001")

# ── Тарифы ────────────────────────────────────────────────────────────────────
TIER_OWNER      = "owner"
TIER_VIP        = "vip"
TIER_SUBSCRIBER = "subscriber"
TIER_TRIAL      = "trial"
TIER_BLOCKED    = "blocked"

TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "3"))

# (messages/day, images/day, videos/day)
TIER_LIMITS = {
    TIER_OWNER:      (99999, 99999, 99999),
    TIER_VIP:        (99999, 99999, 99999),
    TIER_SUBSCRIBER: (int(os.getenv("SUB_MSG", "100")),
                      int(os.getenv("SUB_IMG", "5")),
                      int(os.getenv("SUB_VID", "2"))),
    TIER_TRIAL:      (int(os.getenv("TRIAL_MSG", "20")),
                      int(os.getenv("TRIAL_IMG", "2")),
                      int(os.getenv("TRIAL_VID", "0"))),
    TIER_BLOCKED:    (0, 0, 0),
}
TIER_NAMES = {
    TIER_OWNER:      "Владелец",
    TIER_VIP:        "VIP",
    TIER_SUBSCRIBER: "Подписчик",
    TIER_TRIAL:      "Тестовый период",
    TIER_BLOCKED:    "Заблокирован",
}

# ── Глобальный дневной бюджет (все пользователи суммарно, кроме owner/vip) ────
GLOBAL_DAILY_MSG = int(os.getenv("GLOBAL_DAILY_MSG", "500"))
GLOBAL_DAILY_IMG = int(os.getenv("GLOBAL_DAILY_IMG", "25"))
GLOBAL_DAILY_VID = int(os.getenv("GLOBAL_DAILY_VID", "5"))
GLOBAL_LIMITS = {"messages": GLOBAL_DAILY_MSG, "images": GLOBAL_DAILY_IMG, "videos": GLOBAL_DAILY_VID}

# ── Контакт для подписки ──────────────────────────────────────────────────────
CONTACT = os.getenv("CONTACT", "@pkazakoff")

# ── Темы по умолчанию ─────────────────────────────────────────────────────────
DEFAULT_TOPICS = {
    "работа":     "Проекты, задачи, клиенты",
    "личное":     "Семья, быт, здоровье",
    "творческое": "Контент, идеи, AI-генерация",
}

REMINDER_HARD = [
    "поставь в календарь", "добавь в календарь",
    "поставь напоминание", "добавь напоминание",
    "поставь событие", "создай событие",
    "поставь встречу", "добавь встречу", "запомни дату",
]
REMINDER_SOFT = ["напомни", "напоминание", "не забыть", "отметь"]
