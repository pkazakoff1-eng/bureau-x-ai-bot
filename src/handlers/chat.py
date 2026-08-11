"""BX Assistant — основной чат: текст, голос, фото, документы, напоминания."""
import base64
import logging
import os

from telegram import Update
from telegram.ext import ContextTypes

from .. import db
from ..access import gate, spend
from ..services import ai, misc
from .commands import cmd_image, cmd_status, cmd_topics, cmd_video
from .media_flows import handle_photo_states, handle_text_states

logger = logging.getLogger(__name__)

# кнопки старого ReplyKeyboard-меню (совместимость)
OLD_BTN_MAP = {
    "🎨 генерация фото": cmd_image,
    "🎬 генерация видео": cmd_video,
    "📚 темы": cmd_topics,
    "💎 подписка": cmd_status,
    "❓ помощь": cmd_status,
}


async def _chat_reply(update, user_id, topic, text, search_ctx=""):
    db.save_message(user_id, "user", text, topic)
    await ai.update_summary(user_id)
    history = db.get_history(user_id)
    if search_ctx and history:
        history[-1]["content"] += f"\n\n[Поиск]:\n{search_ctx}"
    reply = await ai.chat(user_id, topic, history)
    db.save_message(user_id, "assistant", reply, topic)
    spend(user_id, "messages")
    await ai.maybe_update_notes(user_id, topic)
    await update.message.reply_text(reply)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text
    try:
        # старые кнопки
        tl_btn = user_text.lower().strip()
        for btn_text, handler in OLD_BTN_MAP.items():
            if tl_btn == btn_text:
                await handler(update, context)
                return

        # медиа-состояния
        if await handle_text_states(update, context):
            return

        if not await gate(update, context, "messages"):
            return

        # напоминания
        if await misc.detect_reminder(user_text):
            dt, title = await misc.parse_reminder(user_text)
            if dt and title:
                await misc.send_reminder_ics(update, context, title, dt, user_id)
            else:
                await update.message.reply_text(
                    "Не смог разобрать дату.\nПример: напомни 15 июля в 10:00 встреча с клиентом")
            return

        # предпочтения
        tl = user_text.lower()
        if any(w in tl for w in ["запомни что", "запомни:", "всегда отвечай", "никогда не пиши"]):
            db.save_prefs(user_id, (db.get_prefs(user_id) + "\n" + user_text).strip())
            await update.message.reply_text("✅ Запомнил!")
            return

        pinned = db.get_user_topic(user_id)
        topic = pinned if pinned else await ai.resolve_topic(user_text, user_id)

        search_ctx = ""
        if await ai.needs_search(user_text, misc.search_keyword_hit(user_text)):
            await update.message.reply_text("🔍 Ищу...")
            search_ctx = await misc.web_search(user_text)

        await _chat_reply(update, user_id, topic, user_text, search_ctx)

    except Exception as e:
        logger.exception(f"Text error: {e}")
        await update.message.reply_text("⚠️ Ошибка. Попробуй ещё раз или /reset")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        if not await gate(update, context, "messages"):
            return
        await update.message.reply_text("🎙 Транскрибирую...")
        voice = update.message.voice
        file = await context.bot.get_file(voice.file_id)
        path = f"/tmp/voice_{user_id}.ogg"
        await file.download_to_drive(path)
        text = await misc.transcribe(path)
        os.remove(path)
        if not text:
            await update.message.reply_text("Не смог распознать.")
            return
        await update.message.reply_text(f"📝 Распознал: {text}")

        if await misc.detect_reminder(text):
            dt, title = await misc.parse_reminder(text)
            if dt and title:
                await misc.send_reminder_ics(update, context, title, dt, user_id)
            return

        pinned = db.get_user_topic(user_id)
        topic = pinned if pinned else await ai.resolve_topic(text, user_id)
        await _chat_reply(update, user_id, topic, text)
    except Exception as e:
        logger.exception(f"Voice: {e}")
        await update.message.reply_text("⚠️ Ошибка с голосовым.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        if await handle_photo_states(update, context):
            return

        # анализ фото через Claude Vision
        if not await gate(update, context, "messages"):
            return
        await update.message.reply_text("🔍 Анализирую фото...")
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        file_bytes = await file.download_as_bytearray()
        image_data = base64.standard_b64encode(file_bytes).decode("utf-8")
        caption = update.message.caption or "Что на этом фото?"
        pinned = db.get_user_topic(user_id)
        topic = pinned if pinned else await ai.resolve_topic(caption, user_id)
        history = db.get_history(user_id)
        messages = history + [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data}},
            {"type": "text", "text": caption}
        ]}]
        reply = await ai.chat(user_id, topic, messages)
        db.save_message(user_id, "user", f"пользователь прислал фото: {caption}", topic)
        db.save_message(user_id, "assistant", reply, topic)
        spend(user_id, "messages")
        await update.message.reply_text(reply)
    except Exception as e:
        logger.exception(f"Photo: {e}")
        await update.message.reply_text("⚠️ Ошибка с фото.")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        if not await gate(update, context, "messages"):
            return
        doc = update.message.document
        caption = update.message.caption or ""
        pinned = db.get_user_topic(user_id)
        topic = pinned if pinned else "общее"
        if doc.mime_type == "application/pdf":
            await update.message.reply_text("📄 Читаю PDF...")
            file = await context.bot.get_file(doc.file_id)
            file_bytes = await file.download_as_bytearray()
            pdf_data = base64.standard_b64encode(file_bytes).decode("utf-8")
            messages = db.get_history(user_id) + [{"role": "user", "content": [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_data}},
                {"type": "text", "text": caption or "Проанализируй документ."}
            ]}]
        elif doc.mime_type and doc.mime_type.startswith("image/"):
            await update.message.reply_text("🔍 Анализирую изображение...")
            file = await context.bot.get_file(doc.file_id)
            file_bytes = await file.download_as_bytearray()
            image_data = base64.standard_b64encode(file_bytes).decode("utf-8")
            history = db.get_history(user_id)
            messages = history + [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": doc.mime_type, "data": image_data}},
                {"type": "text", "text": caption or "Что на этом изображении?"}
            ]}]
        else:
            await update.message.reply_text("Умею читать PDF и изображения.")
            return
        reply = await ai.chat(user_id, topic, messages)
        db.save_message(user_id, "user", f"пользователь прислал файл: {caption}", topic)
        db.save_message(user_id, "assistant", reply, topic)
        spend(user_id, "messages")
        await update.message.reply_text(reply)
    except Exception as e:
        logger.exception(f"Document: {e}")
        await update.message.reply_text("⚠️ Ошибка с файлом.")


async def check_reminders(context):
    for rid, user_id, title in db.get_due_reminders():
        try:
            await context.bot.send_message(chat_id=user_id, text=f"⏰ Напоминание: {title}")
            db.mark_reminder_fired(rid)
        except Exception as e:
            logger.error(f"Reminder: {e}")
