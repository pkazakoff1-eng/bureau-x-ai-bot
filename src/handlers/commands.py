"""BX Assistant — команды: /start /help /reset /status /admin /topics /image /video."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import ContextTypes

from .. import db
from ..access import gate, FIELD_RU
from ..config import (CONTACT, TIER_LIMITS, TIER_NAMES, TIER_OWNER, TIER_VIP,
                      TIER_TRIAL, TIER_BLOCKED, TRIAL_DAYS)


def tier_info_text(user_id):
    tier = db.get_tier(user_id)
    limits = TIER_LIMITS[tier]
    usage = db.get_usage(user_id)
    name = TIER_NAMES[tier]
    lines = []
    if limits[0] > 9000:
        return f"💎 Тариф: {name} (без ограничений)"
    lines.append(f"💎 Тариф: {name}")
    if tier == TIER_TRIAL:
        left = db.trial_days_left(user_id)
        lines.append(f"⏳ Осталось дней: {left} из {TRIAL_DAYS}")
    lines.append(f"💬 Сообщения: {usage[0]}/{limits[0]}")
    lines.append(f"🎨 Картинки: {usage[1]}/{limits[1]}")
    lines.append(f"🎬 Видео: {usage[2]}/{limits[2]}" if limits[2] else "🎬 Видео: по подписке")
    return "\n".join(lines)


IMAGE_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("📝 Текст → Фото", callback_data="media:img_t2i")],
    [InlineKeyboardButton("🖼 Фото → Фото (редактирование)", callback_data="media:img_i2i")],
])
VIDEO_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("📝 Текст → Видео", callback_data="media:vid_t2v")],
    [InlineKeyboardButton("🎬 Фото → Видео", callback_data="media:vid_i2v")],
])


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    name = user.full_name or "Пользователь"
    username = f"@{user.username}" if user.username else ""
    is_new = db.ensure_user(user_id, name, username)
    await update.message.reply_text(".", reply_markup=ReplyKeyboardRemove())
    trial_note = ""
    if db.get_tier(user_id) == TIER_TRIAL:
        trial_note = f"\n🎁 Тебе доступен тестовый период — {TRIAL_DAYS} дня.\n"
    await update.message.reply_text(
        f"Привет, {name}! Я BX Assistant 🤖\n\n"
        f"Умею:\n"
        f"• Отвечать на вопросы и искать актуальную инфу в интернете\n"
        f"• Понимать голосовые сообщения\n"
        f"• Анализировать фото и PDF\n"
        f"• Ставить напоминания → календарь телефона\n"
        f"• Генерировать фото /image и видео /video\n"
        f"• Вести темы раздельно /topics\n"
        f"{trial_note}\n"
        f"{tier_info_text(user_id)}\n\n"
        f"Полный список команд: /help")
    if is_new and db.get_tier(user_id) == TIER_TRIAL:
        from ..config import OWNER_ID
        try:
            total = len(db.list_users())
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=f"🆕 Новый: {name} {username} ({user_id})\nВсего: {total}")
        except Exception:
            pass


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 BX Assistant — команды:\n\n"
        "/image — генерация и редактирование фото\n"
        "/video — генерация видео\n"
        "/topics — тематические заметки при общей истории диалога\n"
        "/status — тариф и лимиты на сегодня\n"
        "/reset — очистить общую историю диалога\n\n"
        "Просто пиши текстом или голосом — отвечу.\n"
        "Пришли фото или PDF — проанализирую.\n"
        "Скажи «напомни …» — создам событие для календаря.\n\n"
        f"Вопросы и подписка: {CONTACT}")


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data.clear()
    db.clear_history(user_id)
    await update.message.reply_text("🗑 Общая история диалога очищена. Начнём заново!")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(tier_info_text(update.effective_user.id))


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if db.get_tier(user_id) not in (TIER_OWNER, TIER_VIP):
        return
    args = context.args or []
    if not args or args[0] == "list":
        lines = ["👥 Пользователи:"]
        for uid, t, n, un, ts in db.list_users():
            usage = db.get_usage(uid)
            lines.append(f"  {TIER_NAMES.get(t, t)}: {n} {un} ({uid}) — {usage[0]} msg, "
                         f"{usage[1]} img, {usage[2]} vid")
        g = db.get_global_usage()
        from ..config import GLOBAL_LIMITS
        lines.append(f"\n🌍 Сегодня всего: {g[0]}/{GLOBAL_LIMITS['messages']} msg, "
                     f"{g[1]}/{GLOBAL_LIMITS['images']} img, {g[2]}/{GLOBAL_LIMITS['videos']} vid")
        await update.message.reply_text("\n".join(lines))
    elif args[0] == "set" and len(args) >= 3:
        try:
            target = int(args[1])
            new_tier = args[2].lower()
            if new_tier not in TIER_LIMITS:
                await update.message.reply_text(f"Тиры: {', '.join(TIER_LIMITS)}")
                return
            db.set_tier(target, new_tier)
            await update.message.reply_text(f"✅ {target} → {TIER_NAMES[new_tier]}")
            try:
                if new_tier not in (TIER_BLOCKED,):
                    await context.bot.send_message(
                        chat_id=target,
                        text=f"🎉 Твой тариф обновлён: {TIER_NAMES[new_tier]}")
            except Exception:
                pass
        except ValueError:
            await update.message.reply_text("Неверный ID")
    else:
        await update.message.reply_text(
            "/admin list — пользователи и расход за сегодня\n"
            "/admin set <id> <tier>\n"
            f"Тиры: {', '.join(TIER_LIMITS)}")


async def cmd_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await gate(update, context, "images"):
        return
    await update.message.reply_text("🎨 Выбери режим:", reply_markup=IMAGE_KB)


async def cmd_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await gate(update, context, "videos"):
        return
    await update.message.reply_text("🎬 Выбери режим:", reply_markup=VIDEO_KB)


async def cmd_topics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    topics = db.get_user_topics(user_id)
    current = db.get_user_topic(user_id) or "авто"
    keyboard = []
    for name in topics:
        marker = "✅ " if name == current else ""
        keyboard.append([InlineKeyboardButton(f"{marker}{name.upper()}", callback_data=f"topic:{name}")])
    keyboard.append([InlineKeyboardButton("🔄 Авто-определение", callback_data="topic:auto")])
    keyboard.append([InlineKeyboardButton("➕ Новая тема", callback_data="topic:new")])
    await update.message.reply_text(
        f"Темы (активна: *{current}*)\nКаждая тема — отдельная история:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown")
