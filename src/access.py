"""BX Assistant — контроль доступа: тарифы, trial, дневные и глобальные лимиты."""
import logging
from datetime import date

from . import db
from .config import (CONTACT, OWNER_ID, TIER_BLOCKED, TIER_OWNER, TIER_VIP,
                     TIER_TRIAL, TRIAL_DAYS)

logger = logging.getLogger(__name__)
_budget_alerted = {"day": None}

FIELD_RU = {"messages": "сообщений", "images": "генераций фото", "videos": "генераций видео"}

TRIAL_EXPIRED_TEXT = (
    "⏳ Тестовый период ({days} дн.) закончился.\n\n"
    "Понравился ассистент? Для продолжения — напиши {contact}, "
    "подключим подписку с расширенными лимитами 🚀"
)


def is_unlimited(user_id):
    return db.get_tier(user_id) in (TIER_OWNER, TIER_VIP)


async def gate(update, context, field="messages"):
    """Проверяет доступ. True = можно работать. Лимит списывается ПОСЛЕ успеха
    через spend() — неудачные запросы не сгорают."""
    user = update.effective_user
    user_id = user.id
    name = user.full_name or ""
    username = f"@{user.username}" if user.username else ""
    is_new = db.ensure_user(user_id, name, username)
    if is_new and user_id != OWNER_ID:
        try:
            total = len(db.list_users())
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=f"🆕 Новый пользователь: {name} {username} ({user_id})\nВсего: {total}")
        except Exception:
            pass

    tier = db.get_tier(user_id)

    if tier == TIER_BLOCKED:
        await update.message.reply_text("❌ Доступ закрыт.")
        return False

    if tier == TIER_TRIAL and db.trial_expired(user_id):
        await update.message.reply_text(
            TRIAL_EXPIRED_TEXT.format(days=TRIAL_DAYS, contact=CONTACT))
        return False

    if not db.check_limit(user_id, field):
        limit = db.limit_of(user_id, field)
        if limit == 0:
            await update.message.reply_text(
                f"🔒 Генерация видео доступна по подписке.\nНапиши {CONTACT} для подключения.")
        else:
            await update.message.reply_text(
                f"⚠️ Дневной лимит {FIELD_RU[field]} исчерпан ({limit}/день).\n"
                f"Обновится в полночь. Нужно больше — напиши {CONTACT}.")
        return False

    # Глобальный бюджет-предохранитель (owner/vip не ограничены)
    if not is_unlimited(user_id) and not db.check_global_limit(field):
        await update.message.reply_text(
            "⚠️ Сегодня высокая нагрузка на сервис. Попробуй завтра 🙏")
        today = date.today().isoformat()
        if _budget_alerted["day"] != today:
            _budget_alerted["day"] = today
            try:
                await update.get_bot().send_message(
                    chat_id=OWNER_ID,
                    text=f"🚨 Глобальный дневной бюджет по '{field}' исчерпан — "
                         f"запросы пользователей отклоняются до полуночи.")
            except Exception:
                pass
        return False

    return True


def spend(user_id, field):
    """Списывает единицу лимита после успешного ответа."""
    db.inc_usage(user_id, field, count_global=not is_unlimited(user_id))
