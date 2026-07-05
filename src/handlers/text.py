import logging
import re
import anthropic
from telegram import Update
from telegram.ext import ContextTypes
from src.database.db import get_history, save_message, get_prefs, save_prefs, get_summary, save_summary, get_message_count
from src.services.search import web_search
from src.config import ANTHROPIC_KEY

logger = logging.getLogger(__name__)
client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# Статический base prompt — кешируется Anthropic (cache_control)
SYSTEM_BASE = """Ты личный семейный ассистент Bureau X.

Автоматически определяй категорию каждого сообщения:
- РАБОТА: задачи по видео, монтажу, правкам, клиентам, РМГ, Shot Films
- ИСПАНИЯ: всё про переезд, жизнь в Испании, недвижимость, визы, животные
- ТВОРЧЕСКОЕ: контент для инсты, видео промпты, идеи
- ЛИЧНОЕ: семья, быт, здоровье, личные дела

В начале ответа пиши категорию в скобках, например (ИСПАНИЯ).
Ты умеешь анализировать фото и документы.
Если в контексте есть результаты поиска — используй их для ответа.
Отвечай кратко и по делу."""

# Ключевые слова для активации веб-поиска (вместо отдельного Claude-запроса)
SEARCH_KEYWORDS = [
    'кто', 'что', 'где', 'когда', 'почему', 'как', 'сколько', 'цена', 'новости',
    'события', 'последние', 'сегодня', 'вчера', '2024', '2025', '2026',
    'курс', 'погода', 'результат', 'матч', 'игра', 'выборы', 'закон',
    'документ', 'справка', 'телефон', 'адрес', 'рейтинг', 'отзыв',
    'стоимость', 'рубл', 'доллар', 'евро', 'тенге', 'цена'
]

def get_system(user_id):
    prefs = get_prefs(user_id)
    summary = get_summary(user_id)
    from datetime import datetime
    current_date = datetime.now().strftime("%d.%m.%Y")
    
    dynamic = f"Сегодня {current_date}."
    if summary:
        dynamic += f"\n\nРезюме предыдущих разговоров:\n{summary}"
    if prefs:
        dynamic += f"\n\nПредпочтения пользователя:\n{prefs}"
    
    # Разделяем на кешируемую (статическую) и динамическую части
    return [
        {"type": "text", "text": SYSTEM_BASE, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": dynamic}
    ]

async def update_summary(user_id):
    count = get_message_count(user_id)
    if count % 30 == 0 and count > 0:
        history = get_history(user_id)
        old_summary = get_summary(user_id)
        messages = [{"role": "user", "content": f"Сделай краткое резюме этого диалога в 5 предложениях. Предыдущее резюме: {old_summary}\n\nНовые сообщения:\n" + "\n".join([f"{m['role']}: {m['content']}" for m in history])}]
        response = client.messages.create(model="claude-sonnet-4-6", max_tokens=500, messages=messages)
        save_summary(user_id, response.content[0].text)

def needs_search(text):
    """Keyword-based поиск вместо отдельного Claude-запроса. Экономит ~$0.01-0.05 на каждом сообщении."""
    if len(text.split()) <= 3:
        return False
    text_lower = text.lower()
    return any(kw in text_lower for kw in SEARCH_KEYWORDS)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text
    try:
        if any(word in user_text.lower() for word in ['запомни что', 'запомни:', 'всегда', 'никогда не', 'не используй', 'пиши без']):
            prefs = get_prefs(user_id)
            new_prefs = prefs + '\n' + user_text if prefs else user_text
            save_prefs(user_id, new_prefs)
            await update.message.reply_text("Запомнил!")
            return

        search_context = ""
        if needs_search(user_text):
            await update.message.reply_text("Ищу в интернете...")
            search_context = web_search(user_text)

        save_message(user_id, "user", user_text)
        await update_summary(user_id)
        history = get_history(user_id)

        if search_context:
            history[-1]["content"] += f"\n\n[Результаты поиска]:\n{search_context}"

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            system=get_system(user_id),
            messages=history
        )
        reply = response.content[0].text
        save_message(user_id, "assistant", reply)
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"Text error: {e}")
        await update.message.reply_text("Ошибка, попробуй ещё раз.")
