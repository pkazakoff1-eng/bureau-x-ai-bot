"""BX Assistant — поиск (Tavily), распознавание речи (Whisper), напоминания (.ics)."""
import asyncio
import io
import logging
import uuid
from datetime import date, datetime, timedelta, timezone

from faster_whisper import WhisperModel
from tavily import TavilyClient

from ..config import TAVILY_KEY, REMINDER_HARD, REMINDER_SOFT
from .ai import util
from .. import db

logger = logging.getLogger(__name__)
_tavily = TavilyClient(api_key=TAVILY_KEY)
_whisper = WhisperModel("tiny", device="cpu", compute_type="int8")


# ── Поиск ─────────────────────────────────────────────────────────────────────
def _search_sync(query):
    try:
        result = _tavily.search(query=query[:400], max_results=3)
        return "\n\n".join([r["content"] for r in result["results"][:3]])
    except Exception as e:
        logger.error(f"Search: {e}")
        return ""


async def web_search(query):
    return await asyncio.to_thread(_search_sync, query)


def search_keyword_hit(text):
    keywords = [
        "найди", "поищи", "что сейчас", "актуально", "новости",
        "цена", "стоимость", "сколько стоит", "курс", "погода",
        "виза", "консульство", "требования", "можно ли сейчас",
        "расписание", "рейс", "билеты", "на сегодня",
        "последняя версия", "что нового", "2025", "2026",
    ]
    t = text.lower()
    return any(w in t for w in keywords)


# ── Голос ─────────────────────────────────────────────────────────────────────
def _transcribe_sync(path):
    segments, _ = _whisper.transcribe(path, language="ru")
    return " ".join([s.text for s in segments]).strip()


async def transcribe(path):
    return await asyncio.to_thread(_transcribe_sync, path)


# ── Напоминания ───────────────────────────────────────────────────────────────
async def detect_reminder(text):
    t = text.lower()
    if any(kw in t for kw in REMINDER_HARD):
        return True
    if any(kw in t for kw in REMINDER_SOFT):
        try:
            ans = await util(
                f"Это запрос создать событие в календарь (с датой/временем) "
                f"или просьба вспомнить что-то? Только 'календарь' или 'вспомнить': {text}",
                max_tokens=5)
            return "календарь" in ans.lower()
        except Exception:
            return False
    return False


async def parse_reminder(text):
    today = date.today().strftime("%Y-%m-%d")
    try:
        ans = await util(
            f"Сегодня {today}. Извлеки дату/время и название события.\n"
            f"Ответ СТРОГО: YYYY-MM-DD HH:MM | Название\n"
            f"Если время не указано — 09:00.\nТекст: {text}", max_tokens=80)
        parts = ans.split("|")
        dt = datetime.strptime(parts[0].strip(), "%Y-%m-%d %H:%M")
        title = parts[1].strip() if len(parts) > 1 else text[:60]
        return dt, title
    except Exception as e:
        logger.error(f"parse_reminder: {e}")
        return None, None


def make_ics(title, dt):
    uid = str(uuid.uuid4())
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dtstart = dt.strftime("%Y%m%dT%H%M%S")
    dtend = (dt + timedelta(hours=1)).strftime("%Y%m%dT%H%M%S")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//BX Bot//RU",
        "BEGIN:VEVENT",
        f"UID:{uid}", f"DTSTAMP:{stamp}", f"DTSTART:{dtstart}", f"DTEND:{dtend}",
        f"SUMMARY:{title}",
        "BEGIN:VALARM", "TRIGGER:-PT30M", "ACTION:DISPLAY", f"DESCRIPTION:{title}", "END:VALARM",
        "END:VEVENT", "END:VCALENDAR",
    ]
    return "\r\n".join(lines)


async def send_reminder_ics(update, context, title, dt, user_id):
    ics_bytes = io.BytesIO(make_ics(title, dt).encode("utf-8"))
    ics_bytes.name = "reminder.ics"
    db.save_reminder(user_id, title, dt.strftime("%Y-%m-%d %H:%M"))
    await update.message.reply_text(
        f"✅ Напоминание создано!\n"
        f"📅 {title}\n"
        f"🕐 {dt.strftime('%d.%m.%Y %H:%M')}\n\n"
        f"Открой .ics чтобы добавить в календарь:")
    await context.bot.send_document(
        chat_id=update.effective_chat.id, document=ics_bytes, filename="reminder.ics")
