import logging
import base64
import os
import anthropic
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes
from src.database.db import get_history, save_message, get_prefs, get_summary
from src.services.whisper import transcribe
from src.config import ANTHROPIC_KEY

logger = logging.getLogger(__name__)
client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# Статический промпт для анализа файлов — кешируется
MEDIA_ANALYSIS_BASE = """Ты личный семейный ассистент Bureau X.

Проанализируй предоставленный файл/фото максимально подробно:
- Выдели ВЕСЬ текст, который видишь (дословно, если это документ)
- Укажи все цифры, даты, суммы, имена, адреса, названия
- Опиши структуру документа (заголовки, разделы, таблицы)
- Если это фото — опиши все детали, объекты, людей, текст на изображении
- Не сокращай, не пересказывай кратко — сохраняй полноту для последующего использования

В начале ответа пиши категорию в скобках, например (РАБОТА).
Этот разбор сохранится в память диалога — он должен быть полным, чтобы позже можно было спросить детали без повторной отправки файла."""

def get_media_system():
    current_date = datetime.now().strftime("%d.%m.%Y")
    dynamic = f"Сегодня {current_date}."
    return [
        {"type": "text", "text": MEDIA_ANALYSIS_BASE, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": dynamic}
    ]

def get_system(user_id):
    from src.handlers.text import get_system as _get_system
    return _get_system(user_id)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        await update.message.reply_text("Голосовое получил, транскрибирую...")
        file = await context.bot.get_file(update.message.voice.file_id)
        path = f"/tmp/voice_{user_id}.ogg"
        await file.download_to_drive(path)
        text = transcribe(path)
        os.remove(path)
        if not text.strip():
            await update.message.reply_text("Не смог распознать.")
            return
        await update.message.reply_text(f"Распознал: {text}")
        save_message(user_id, "user", text)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            system=get_system(user_id),
            messages=get_history(user_id)
        )
        reply = response.content[0].text
        save_message(user_id, "assistant", reply)
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"Voice error: {e}")
        await update.message.reply_text("Ошибка с голосовым.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        await update.message.reply_text("Фото получил, анализирую...")
        file = await context.bot.get_file(update.message.photo[-1].file_id)
        file_bytes = await file.download_as_bytearray()
        image_data = base64.standard_b64encode(file_bytes).decode('utf-8')
        caption = update.message.caption or "Что на этом фото? Проанализируй подробно."
        history = get_history(user_id)
        messages = history + [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data}},
            {"type": "text", "text": caption}
        ]}]
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system=get_media_system(),
            messages=messages
        )
        reply = response.content[0].text
        save_message(user_id, "user", f"[Разбор фото] {caption}")
        save_message(user_id, "assistant", reply)
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"Photo error: {e}")
        await update.message.reply_text("Ошибка с фото.")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        doc = update.message.document
        caption = update.message.caption or ""
        history = get_history(user_id)
        if doc.mime_type == 'application/pdf':
            await update.message.reply_text("PDF получил, читаю...")
            file = await context.bot.get_file(doc.file_id)
            file_bytes = await file.download_as_bytearray()
            pdf_data = base64.standard_b64encode(file_bytes).decode('utf-8')
            text = caption or "Проанализируй этот документ подробно."
            messages = history + [{"role": "user", "content": [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_data}},
                {"type": "text", "text": text}
            ]}]
        elif doc.mime_type and doc.mime_type.startswith('image/'):
            await update.message.reply_text("Изображение получил, анализирую...")
            file = await context.bot.get_file(doc.file_id)
            file_bytes = await file.download_as_bytearray()
            image_data = base64.standard_b64encode(file_bytes).decode('utf-8')
            text = caption or "Что на этом изображении?"
            messages = history + [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": doc.mime_type, "data": image_data}},
                {"type": "text", "text": text}
            ]}]
        else:
            await update.message.reply_text("Пока умею читать только PDF и изображения.")
            return
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system=get_media_system(),
            messages=messages
        )
        reply = response.content[0].text
        if doc.mime_type == 'application/pdf':
            save_message(user_id, "user", f"[Разбор файла] {caption}")
        else:
            save_message(user_id, "user", f"[Разбор фото] {caption}")
        save_message(user_id, "assistant", reply)
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"Document error: {e}")
        await update.message.reply_text("Ошибка с файлом.")
