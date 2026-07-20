"""BX Assistant — Claude AI: чат, классификация, заметки, резюме."""
import asyncio
import logging
from datetime import date, datetime

STICKY_TOPIC_SECONDS = 900  # 15 минут: продолжение разговора не меняет тему

import anthropic

from ..config import ANTHROPIC_KEY, CHAT_MODEL, UTIL_MODEL
from .. import db

logger = logging.getLogger(__name__)
_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)


async def ai_create(**kw):
    """Неблокирующий вызов Anthropic API."""
    return await asyncio.to_thread(lambda: _client.messages.create(**kw))


def _sync_util(prompt, max_tokens):
    resp = _client.messages.create(
        model=UTIL_MODEL, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}])
    return resp.content[0].text.strip()


async def util(prompt, max_tokens=20):
    return await asyncio.to_thread(_sync_util, prompt, max_tokens)


# ── System prompt ─────────────────────────────────────────────────────────────
def get_system(user_id, topic=None):
    today = date.today().strftime("%d %B %Y")
    prefs = db.get_prefs(user_id)
    summary = db.get_summary(user_id)
    notes = db.get_topic_notes(user_id, topic) if topic and topic != "общее" else ""

    sys = (
        f"Ты личный ассистент. Сегодня: {today}.\n"
        f"Отвечай на ЛЮБОЙ вопрос пользователя — нет запрещённых тем.\n"
        f"Ты можешь: анализировать фото/PDF когда они приложены, создавать напоминания (.ics).\n"
        f"Команды бота: /image (генерация фото), /video (генерация видео), /topics (темы), /reset (очистить историю).\n"
        f"Если просят сгенерировать фото или видео — направляй на /image или /video.\n"
        f"ВАЖНО: НЕ пиши что пользователь отправил фото, если ты его не видишь в текущем сообщении.\n"
        f"НЕ проси пользователя повторно прикреплять фото если он тебя ни о чём таком не просил.\n"
        f"НИКОГДА не пиши 'user:' или 'assistant:' в своём ответе. Не имитируй продолжение диалога.\n"
        f"Отвечай кратко и по делу."
    )
    if topic and topic != "общее":
        sys += f"\n\nТекущая тема: [{topic.upper()}]. История ведётся отдельно по этой теме."
    if notes:
        sys += f"\n\nЗаметки по теме:\n{notes}"
    if summary:
        sys += f"\n\nКраткое резюме прошлых разговоров:\n{summary}"
    if prefs:
        sys += f"\n\nПредпочтения пользователя:\n{prefs}"
    return [{"type": "text", "text": sys, "cache_control": {"type": "ephemeral"}}]


async def chat(user_id, topic, messages):
    """Основной чат-вызов. Возвращает текст ответа."""
    resp = await ai_create(
        model=CHAT_MODEL, max_tokens=1200,
        stop_sequences=["user:", "\nuser:", "\nUser:"],
        system=get_system(user_id, topic), messages=messages)
    return resp.content[0].text.strip()


# ── Классификация ─────────────────────────────────────────────────────────────
async def resolve_topic(text, user_id):
    """Липкая тема: если разговор продолжается (<15 мин с последнего сообщения),
    остаёмся в текущей теме — не рвём один диалог на части."""
    row = db.last_topic_info(user_id)
    if row:
        try:
            last_ts = datetime.fromisoformat(str(row[1]).split(".")[0])
            age = (datetime.utcnow() - last_ts).total_seconds()
            if 0 <= age < STICKY_TOPIC_SECONDS:
                return row[0]
        except (ValueError, TypeError):
            pass
    return await detect_topic(text, user_id)


async def detect_topic(text, user_id):
    topics = db.get_user_topics(user_id)
    topic_list = "\n".join([f"- {name}: {desc}" for name, desc in topics.items()])
    try:
        detected = (await util(
            f"Определи тему сообщения из списка:\n{topic_list}\n\n"
            f"Сообщение: {text}\n\nОтветь ТОЛЬКО одним словом — название темы или 'общее'."
        )).lower().strip(".")
        if detected in topics:
            return detected
        for t in topics:
            if t in detected or detected in t:
                return t
    except Exception:
        pass
    return "общее"


async def needs_search(text, keyword_hit):
    if keyword_hit:
        return True
    if len(text.split()) < 5:
        return False
    try:
        ans = await util(
            f"Нужен ли веб-поиск для ответа? Только 'да' или 'нет': {text}", max_tokens=5)
        return ans.lower().startswith("да")
    except Exception:
        return False


# ── Фоновая память ────────────────────────────────────────────────────────────
async def maybe_update_notes(user_id, topic, history):
    cnt = db.topic_message_count(user_id, topic)
    if cnt > 0 and cnt % 10 == 0:
        old_notes = db.get_topic_notes(user_id, topic)
        dialogue = "\n".join([f"{m['role']}: {str(m['content'])[:200]}" for m in history[-10:]])
        try:
            text = await util(
                f"Ты — модуль памяти бота, НЕ собеседник. Обнови заметки по теме '{topic}'.\n"
                f"Старые заметки: {old_notes}\n\nДиалог:\n{dialogue}\n\n"
                f"ПРАВИЛА:\n"
                f"- Записывай ТОЛЬКО факты, которые сообщил сам user. НИКОГДА не записывай "
                f"предположения, примеры и варианты из реплик assistant.\n"
                f"- Если user поправил факт — записывай исправленную версию, старую удали.\n"
                f"- Выведи ТОЛЬКО текст заметок (тезисы, до 200 слов). Без вступлений, "
                f"извинений, эмодзи и обращений.",
                max_tokens=300)
            db.save_topic_notes(user_id, topic, text)
        except Exception:
            pass


async def update_summary(user_id):
    cnt = db.get_message_count(user_id)
    if cnt > 0 and cnt % 30 == 0:
        history = db.get_history(user_id)
        old = db.get_summary(user_id)
        try:
            text = await util(
                f"Ты — модуль памяти бота, НЕ собеседник. Сделай краткое фактическое резюме "
                f"диалога (3-5 предложений): кто пользователь, что обсуждали, что решили.\n"
                f"Предыдущее резюме: {old}\n\nДиалог:\n"
                + "\n".join([f"{m['role']}: {str(m['content'])[:200]}" for m in history])
                + "\n\nВыведи ТОЛЬКО текст резюме. Без вступлений, эмодзи и обращений. "
                f"Не продолжай диалог и не отвечай на вопросы из него.",
                max_tokens=300)
            db.save_summary(user_id, text)
        except Exception:
            pass
