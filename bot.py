import anthropic
import asyncio
import requests
import time
import sqlite3
import base64
import os
import io
import uuid
import logging
from datetime import date, datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, CallbackQueryHandler, filters, ContextTypes
from faster_whisper import WhisperModel
from tavily import TavilyClient
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_KEY  = os.environ["ANTHROPIC_KEY"]
TAVILY_KEY     = os.environ["TAVILY_KEY"]
WAVESPEED_KEY  = os.environ["WAVESPEED_KEY"]

OWNER_ID = 285198612
VIP_ID   = 587290278

TIER_OWNER      = "owner"
TIER_VIP        = "vip"
TIER_SUBSCRIBER = "subscriber"
TIER_BETA       = "beta"
TIER_BLOCKED    = "blocked"

TIER_LIMITS = {
    TIER_OWNER:      (99999, 99999, 99999),
    TIER_VIP:        (99999, 99999, 99999),
    TIER_SUBSCRIBER: (100, 5, 2),
    TIER_BETA:       (20, 3, 1),
    TIER_BLOCKED:    (0, 0, 0),
}
TIER_NAMES = {
    TIER_OWNER:      "Владелец",
    TIER_VIP:        "VIP",
    TIER_SUBSCRIBER: "Подписчик",
    TIER_BETA:       "Бета-тестер",
    TIER_BLOCKED:    "Заблокирован",
}

DEFAULT_TOPICS = {
    "работа":     "Shot Films, РМГ, видеопроекты, монтаж, клиенты",
    "испания":    "Переезд, визы, жильё, жизнь в Испании",
    "творческое": "Контент, идеи, AI-генерация, инста",
    "личное":     "Семья, быт, здоровье",
    "бот":        "Разработка бота, технические вопросы",
}

REMINDER_HARD = [
    "поставь в календарь", "добавь в календарь",
    "поставь напоминание", "добавь напоминание",
    "поставь событие", "создай событие",
    "поставь встречу", "добавь встречу", "запомни дату",
]
REMINDER_SOFT = ["напомни", "напоминание", "не забыть", "отметь"]

ai     = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
tavily = TavilyClient(api_key=TAVILY_KEY)
whisper = WhisperModel("tiny", device="cpu", compute_type="int8")

async def ai_create(**kw):
    return await asyncio.to_thread(lambda: ai.messages.create(**kw))

# ══════════════════════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════════════════════
def init_db():
    conn = sqlite3.connect("memory.db")
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, role TEXT, content TEXT,
        topic TEXT DEFAULT 'общее',
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS preferences
        (user_id INTEGER PRIMARY KEY, prefs TEXT DEFAULT '')""")
    c.execute("""CREATE TABLE IF NOT EXISTS summaries
        (user_id INTEGER PRIMARY KEY, summary TEXT DEFAULT '',
         topic TEXT DEFAULT 'общее',
         updated DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS topics
        (user_id INTEGER, name TEXT, description TEXT DEFAULT '',
         PRIMARY KEY(user_id, name))""")
    c.execute("""CREATE TABLE IF NOT EXISTS topic_notes
        (user_id INTEGER, topic TEXT, notes TEXT DEFAULT '',
         updated DATETIME DEFAULT CURRENT_TIMESTAMP,
         PRIMARY KEY(user_id, topic))""")
    c.execute("""CREATE TABLE IF NOT EXISTS user_topic
        (user_id INTEGER PRIMARY KEY, current_topic TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, title TEXT, remind_at DATETIME,
        fired INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        tier TEXT DEFAULT 'beta',
        name TEXT DEFAULT '',
        username TEXT DEFAULT '',
        created DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS daily_usage (
        user_id INTEGER, day TEXT,
        messages INTEGER DEFAULT 0,
        images INTEGER DEFAULT 0,
        videos INTEGER DEFAULT 0,
        PRIMARY KEY(user_id, day))""")

    # Миграции
    for sql in [
        "ALTER TABLE users ADD COLUMN tier TEXT DEFAULT 'beta'",
        "ALTER TABLE users ADD COLUMN name TEXT DEFAULT ''",
        "ALTER TABLE topics ADD COLUMN description TEXT DEFAULT ''",
        "ALTER TABLE messages ADD COLUMN topic TEXT DEFAULT 'общее'",
        "ALTER TABLE reminders ADD COLUMN title TEXT DEFAULT ''",
        "ALTER TABLE reminders ADD COLUMN fired INTEGER DEFAULT 0",
    ]:
        try:
            c.execute(sql)
        except Exception:
            pass

    c.execute("INSERT OR IGNORE INTO users (user_id, tier, name) VALUES (?,?,?)", (OWNER_ID, TIER_OWNER, "Pavel"))
    c.execute("INSERT OR IGNORE INTO users (user_id, tier, name) VALUES (?,?,?)", (VIP_ID, TIER_VIP, "Wife"))
    conn.commit()
    conn.close()

# ── Messages ──────────────────────────────────────────────────────────────────
def get_history(user_id, topic=None, limit=20):
    """
    Возвращает историю, фильтруя [фото]-записи из СТАРЫХ диалогов.
    Новые фото сохраняются как 'пользователь прислал фото: <caption>'.
    """
    conn = sqlite3.connect("memory.db")
    c = conn.cursor()
    if topic and topic != "общее":
        c.execute("""SELECT role, content FROM messages
                     WHERE user_id=? AND topic=?
                     ORDER BY timestamp DESC LIMIT ?""", (user_id, topic, limit))
    else:
        c.execute("""SELECT role, content FROM messages
                     WHERE user_id=?
                     ORDER BY timestamp DESC LIMIT ?""", (user_id, limit))
    rows = c.fetchall()
    conn.close()
    messages = []
    for role, content in reversed(rows):
        # Очищаем старые записи '[фото] ...' — они путают Claude
        if content and content.startswith("[фото]"):
            caption = content[6:].strip()
            content = f"пользователь прислал фото{': ' + caption if caption else ''}"
        messages.append({"role": role, "content": content})
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages

def save_message(user_id, role, content, topic="общее"):
    conn = sqlite3.connect("memory.db")
    c = conn.cursor()
    c.execute("INSERT INTO messages (user_id, role, content, topic) VALUES (?,?,?,?)",
              (user_id, role, content, topic))
    conn.commit()
    conn.close()

def clear_history(user_id, topic=None):
    conn = sqlite3.connect("memory.db")
    c = conn.cursor()
    if topic:
        c.execute("DELETE FROM messages WHERE user_id=? AND topic=?", (user_id, topic))
    else:
        c.execute("DELETE FROM messages WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def get_message_count(user_id):
    conn = sqlite3.connect("memory.db")
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM messages WHERE user_id=?", (user_id,))
    n = c.fetchone()[0]
    conn.close()
    return n

# ── Prefs / Summary ───────────────────────────────────────────────────────────
def get_prefs(user_id):
    conn = sqlite3.connect("memory.db")
    c = conn.cursor()
    c.execute("SELECT prefs FROM preferences WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else ""

def save_prefs(user_id, prefs):
    conn = sqlite3.connect("memory.db")
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO preferences (user_id, prefs) VALUES (?,?)", (user_id, prefs))
    conn.commit()
    conn.close()

def get_summary(user_id):
    conn = sqlite3.connect("memory.db")
    c = conn.cursor()
    c.execute("SELECT summary FROM summaries WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else ""

def save_summary(user_id, summary):
    conn = sqlite3.connect("memory.db")
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO summaries (user_id, summary, updated) VALUES (?,?,CURRENT_TIMESTAMP)",
              (user_id, summary))
    conn.commit()
    conn.close()

# ── Tiers ─────────────────────────────────────────────────────────────────────
def get_tier(user_id):
    conn = sqlite3.connect("memory.db")
    c = conn.cursor()
    c.execute("SELECT tier FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else TIER_BETA

def set_tier(user_id, tier, name="", username=""):
    conn = sqlite3.connect("memory.db")
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (user_id, tier, name, username) VALUES (?,?,?,?)",
              (user_id, tier, name, username))
    conn.commit()
    conn.close()

def ensure_user(user_id, name="", username=""):
    conn = sqlite3.connect("memory.db")
    c = conn.cursor()
    c.execute("SELECT tier FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        set_tier(user_id, TIER_BETA, name, username)
        return True
    return False

def get_usage(user_id):
    today = date.today().isoformat()
    conn = sqlite3.connect("memory.db")
    c = conn.cursor()
    c.execute("SELECT messages, images, videos FROM daily_usage WHERE user_id=? AND day=?", (user_id, today))
    row = c.fetchone()
    conn.close()
    return row if row else (0, 0, 0)

def inc_usage(user_id, field):
    today = date.today().isoformat()
    conn = sqlite3.connect("memory.db")
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO daily_usage (user_id, day) VALUES (?,?)", (user_id, today))
    c.execute(f"UPDATE daily_usage SET {field}={field}+1 WHERE user_id=? AND day=?", (user_id, today))
    conn.commit()
    conn.close()

def check_limit(user_id, field):
    tier = get_tier(user_id)
    idx = {"messages": 0, "images": 1, "videos": 2}[field]
    return get_usage(user_id)[idx] < TIER_LIMITS[tier][idx]

def tier_info_text(user_id):
    tier = get_tier(user_id)
    limits = TIER_LIMITS[tier]
    usage = get_usage(user_id)
    name = TIER_NAMES[tier]
    if limits[0] > 9000:
        return f"Тариф: {name} (без ограничений)"
    return (f"Тариф: {name}\n"
            f"Сообщения: {usage[0]}/{limits[0]}\n"
            f"Картинки: {usage[1]}/{limits[1]}\n"
            f"Видео: {usage[2]}/{limits[2]}")

# ── Topics ────────────────────────────────────────────────────────────────────
def get_user_topic(user_id):
    conn = sqlite3.connect("memory.db")
    c = conn.cursor()
    c.execute("SELECT current_topic FROM user_topic WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def set_user_topic(user_id, topic):
    conn = sqlite3.connect("memory.db")
    c = conn.cursor()
    if topic is None:
        c.execute("DELETE FROM user_topic WHERE user_id=?", (user_id,))
    else:
        c.execute("INSERT OR REPLACE INTO user_topic (user_id, current_topic) VALUES (?,?)", (user_id, topic))
    conn.commit()
    conn.close()

def get_user_topics(user_id):
    conn = sqlite3.connect("memory.db")
    c = conn.cursor()
    c.execute("SELECT name, description FROM topics WHERE user_id=?", (user_id,))
    custom = {r[0]: r[1] for r in c.fetchall()}
    conn.close()
    result = dict(DEFAULT_TOPICS)
    result.update(custom)
    return result

def add_user_topic(user_id, name, description=""):
    conn = sqlite3.connect("memory.db")
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO topics (user_id, name, description) VALUES (?,?,?)",
              (user_id, name.lower(), description))
    conn.commit()
    conn.close()

def get_topic_notes(user_id, topic):
    conn = sqlite3.connect("memory.db")
    c = conn.cursor()
    c.execute("SELECT notes FROM topic_notes WHERE user_id=? AND topic=?", (user_id, topic))
    row = c.fetchone()
    conn.close()
    return row[0] if row else ""

def save_topic_notes(user_id, topic, notes):
    conn = sqlite3.connect("memory.db")
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO topic_notes (user_id, topic, notes, updated) VALUES (?,?,?,CURRENT_TIMESTAMP)",
              (user_id, topic, notes))
    conn.commit()
    conn.close()

def detect_topic(text, user_id):
    topics = get_user_topics(user_id)
    topic_list = "\n".join([f"- {name}: {desc}" for name, desc in topics.items()])
    try:
        resp = ai.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=20,
            messages=[{"role": "user", "content": (
                f"Определи тему сообщения из списка:\n{topic_list}\n\n"
                f"Сообщение: {text}\n\nОтветь ТОЛЬКО одним словом — название темы или 'общее'."
            )}]
        )
        detected = resp.content[0].text.strip().lower().strip(".")
        if detected in topics:
            return detected
        for t in topics:
            if t in detected or detected in t:
                return t
        return "общее"
    except Exception:
        return "общее"

async def maybe_update_notes(user_id, topic, history):
    conn = sqlite3.connect("memory.db")
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM messages WHERE user_id=? AND topic=?", (user_id, topic))
    cnt = c.fetchone()[0]
    conn.close()
    if cnt > 0 and cnt % 10 == 0:
        old_notes = get_topic_notes(user_id, topic)
        dialogue = "\n".join([f"{m['role']}: {m['content'][:200]}" for m in history[-10:]])
        try:
            resp = await ai_create(
                model="claude-haiku-4-5-20251001", max_tokens=300,
                messages=[{"role": "user", "content": (
                    f"Обнови заметки по теме '{topic}'.\nСтарые: {old_notes}\n\n"
                    f"Диалог:\n{dialogue}\n\nТолько важные факты, решения — до 200 слов."
                )}]
            )
            save_topic_notes(user_id, topic, resp.content[0].text)
        except Exception:
            pass

# ── Reminders ─────────────────────────────────────────────────────────────────
def detect_reminder(text):
    t = text.lower()
    if any(kw in t for kw in REMINDER_HARD):
        return True
    if any(kw in t for kw in REMINDER_SOFT):
        try:
            resp = ai.messages.create(
                model="claude-haiku-4-5-20251001", max_tokens=5,
                messages=[{"role": "user", "content": (
                    f"Это запрос создать событие в календарь (с датой/временем) "
                    f"или просьба вспомнить что-то? Только 'календарь' или 'вспомнить': {text}"
                )}]
            )
            return "календарь" in resp.content[0].text.strip().lower()
        except Exception:
            return False
    return False

def parse_reminder(text):
    today = date.today().strftime("%Y-%m-%d")
    try:
        resp = ai.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=80,
            messages=[{"role": "user", "content": (
                f"Сегодня {today}. Извлеки дату/время и название события.\n"
                f"Ответ СТРОГО: YYYY-MM-DD HH:MM | Название\n"
                f"Если время не указано — 09:00.\nТекст: {text}"
            )}]
        )
        parts = resp.content[0].text.strip().split("|")
        dt_str = parts[0].strip()
        title = parts[1].strip() if len(parts) > 1 else text[:60]
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
        return dt, title
    except Exception as e:
        logger.error(f"parse_reminder: {e}")
        return None, None

def make_ics(title, dt):
    uid = str(uuid.uuid4())
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
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

def save_reminder(user_id, title, remind_at):
    conn = sqlite3.connect("memory.db")
    c = conn.cursor()
    c.execute("INSERT INTO reminders (user_id, title, remind_at) VALUES (?,?,?)",
              (user_id, title, remind_at))
    conn.commit()
    conn.close()

def get_due_reminders():
    conn = sqlite3.connect("memory.db")
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    c.execute("SELECT id, user_id, title FROM reminders WHERE fired=0 AND remind_at<=?", (now,))
    rows = c.fetchall()
    conn.close()
    return rows

def mark_reminder_fired(rid):
    conn = sqlite3.connect("memory.db")
    c = conn.cursor()
    c.execute("UPDATE reminders SET fired=1 WHERE id=?", (rid,))
    conn.commit()
    conn.close()

async def send_reminder_ics(update, context, title, dt, user_id):
    ics_bytes = io.BytesIO(make_ics(title, dt).encode("utf-8"))
    ics_bytes.name = "reminder.ics"
    save_reminder(user_id, title, dt.strftime("%Y-%m-%d %H:%M"))
    await update.message.reply_text(
        f"✅ Напоминание создано!\n"
        f"📅 {title}\n"
        f"🕐 {dt.strftime('%d.%m.%Y %H:%M')}\n\n"
        f"Открой .ics чтобы добавить в iPhone Calendar:"
    )
    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=ics_bytes,
        filename="reminder.ics"
    )

# ── Search ────────────────────────────────────────────────────────────────────
def needs_search(text):
    keywords = [
        "найди", "поищи", "что сейчас", "актуально", "новости",
        "цена", "стоимость", "сколько стоит", "курс", "погода",
        "виза", "консульство", "требования", "можно ли сейчас",
        "расписание", "рейс", "билеты", "на сегодня",
        "последняя версия", "что нового", "2024", "2025", "2026",
    ]
    t = text.lower()
    if any(w in t for w in keywords):
        return True
    if len(text.split()) >= 5:
        try:
            resp = ai.messages.create(
                model="claude-haiku-4-5-20251001", max_tokens=5,
                messages=[{"role": "user", "content": f"Нужен ли веб-поиск для ответа? Только 'да' или 'нет': {text}"}]
            )
            return resp.content[0].text.strip().lower().startswith("да")
        except Exception:
            return False
    return False

def web_search(query):
    try:
        result = tavily.search(query=query[:400], max_results=3)
        return "\n\n".join([r["content"] for r in result["results"][:3]])
    except Exception as e:
        logger.error(f"Search: {e}")
        return ""

# ── System prompt ─────────────────────────────────────────────────────────────
def get_system(user_id, topic=None):
    today = date.today().strftime("%d %B %Y")
    prefs = get_prefs(user_id)
    summary = get_summary(user_id)
    notes = get_topic_notes(user_id, topic) if topic and topic != "общее" else ""

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

async def update_summary(user_id):
    cnt = get_message_count(user_id)
    if cnt > 0 and cnt % 30 == 0:
        history = get_history(user_id)
        old = get_summary(user_id)
        try:
            resp = await ai_create(
                model="claude-haiku-4-5-20251001", max_tokens=300,
                messages=[{"role": "user", "content": (
                    f"Сделай краткое резюме диалога (3-5 предложений).\nПредыдущее: {old}\n\n"
                    + "\n".join([f"{m['role']}: {m['content'][:200]}" for m in history])
                )}]
            )
            save_summary(user_id, resp.content[0].text)
        except Exception:
            pass

# ══════════════════════════════════════════════════════════════════════════════
# WAVESPEED
# ══════════════════════════════════════════════════════════════════════════════
WS_HEADERS = {"Authorization": f"Bearer {WAVESPEED_KEY}", "Content-Type": "application/json"}

def ws_poll(request_id, timeout=120):
    url = f"https://api.wavespeed.ai/api/v3/predictions/{request_id}/result"
    for _ in range(timeout // 3):
        time.sleep(3)
        try:
            resp = requests.get(url, headers=WS_HEADERS, timeout=10)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                status = data.get("status")
                if status == "completed":
                    outputs = data.get("outputs", [])
                    return outputs[0] if outputs else None
                elif status == "failed":
                    logger.error(f"WS failed: {data}")
                    return None
        except Exception as e:
            logger.error(f"ws_poll: {e}")
    return None

def generate_image_ws(prompt):
    resp = requests.post(
        "https://api.wavespeed.ai/api/v3/google/nano-banana-2/text-to-image",
        headers=WS_HEADERS,
        json={"prompt": prompt, "size": "2048*2048", "num_images": 1},
        timeout=15
    )
    if resp.status_code != 200:
        logger.error(f"Image t2i error: {resp.text}")
        return None
    rid = resp.json().get("data", {}).get("id")
    return ws_poll(rid, timeout=90) if rid else None

def generate_image_edit_ws(image_url, prompt):
    resp = requests.post(
        "https://api.wavespeed.ai/api/v3/google/nano-banana-2/edit",
        headers=WS_HEADERS,
        json={"image": image_url, "prompt": prompt, "size": "2048*2048"},
        timeout=15
    )
    if resp.status_code != 200:
        logger.error(f"Image edit error: {resp.text}")
        return None
    rid = resp.json().get("data", {}).get("id")
    return ws_poll(rid, timeout=90) if rid else None

def generate_video_from_text_ws(prompt):
    resp = requests.post(
        "https://api.wavespeed.ai/api/v3/bytedance/seedance-2.0-mini/text-to-video",
        headers=WS_HEADERS,
        json={"prompt": prompt, "duration": 5, "resolution": "480p"},
        timeout=15
    )
    if resp.status_code != 200:
        logger.error(f"T2V error: {resp.text}")
        return None
    rid = resp.json().get("data", {}).get("id")
    return ws_poll(rid, timeout=120) if rid else None

def generate_video_ws(image_url, prompt=""):
    resp = requests.post(
        "https://api.wavespeed.ai/api/v3/bytedance/seedance-2.0-mini/image-to-video",
        headers=WS_HEADERS,
        json={
            "image": image_url,
            "prompt": prompt or "cinematic smooth motion",
            "duration": 5,
            "resolution": "480p",
        },
        timeout=15
    )
    if resp.status_code != 200:
        logger.error(f"I2V error: {resp.text}")
        return None
    rid = resp.json().get("data", {}).get("id")
    return ws_poll(rid, timeout=120) if rid else None

async def upload_to_tmpfiles(context, photo_obj):
    file = await context.bot.get_file(photo_obj.file_id)
    file_bytes = await file.download_as_bytearray()
    upload = requests.post(
        "https://tmpfiles.org/api/v1/upload",
        files={"file": ("photo.jpg", bytes(file_bytes), "image/jpeg")},
        timeout=15
    )
    if upload.status_code != 200:
        return None
    page_url = upload.json().get("data", {}).get("url", "")
    return page_url.replace("tmpfiles.org/", "tmpfiles.org/dl/")

async def upload_file_id_to_tmpfiles(context, file_id):
    file = await context.bot.get_file(file_id)
    file_bytes = await file.download_as_bytearray()
    upload = requests.post(
        "https://tmpfiles.org/api/v1/upload",
        files={"file": ("photo.jpg", bytes(file_bytes), "image/jpeg")},
        timeout=15
    )
    if upload.status_code != 200:
        return None
    page_url = upload.json().get("data", {}).get("url", "")
    return page_url.replace("tmpfiles.org/", "tmpfiles.org/dl/")

# ══════════════════════════════════════════════════════════════════════════════
# ACCESS GATE
# ══════════════════════════════════════════════════════════════════════════════
async def check_access(update, field="messages") -> bool:
    user_id = update.effective_user.id
    name = update.effective_user.full_name or ""
    username = f"@{update.effective_user.username}" if update.effective_user.username else ""
    ensure_user(user_id, name, username)
    tier = get_tier(user_id)
    if tier == TIER_BLOCKED:
        await update.message.reply_text("❌ Доступ закрыт.")
        return False
    if not check_limit(user_id, field):
        limit = TIER_LIMITS[tier][{"messages": 0, "images": 1, "videos": 2}[field]]
        await update.message.reply_text(
            f"⚠️ Лимит исчерпан ({limit} {field}/день). Обновится в полночь."
        )
        return False
    inc_usage(user_id, field)
    return True

# ══════════════════════════════════════════════════════════════════════════════
# COMMANDS
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.full_name or "Пользователь"
    username = f"@{update.effective_user.username}" if update.effective_user.username else ""
    is_new = ensure_user(user_id, name, username)
    await update.message.reply_text(".", reply_markup=ReplyKeyboardRemove())
    await update.message.reply_text(
        f"Привет, {name}! Я BX Assistant 🤖\n\n"
        f"Умею:\n"
        f"• Отвечать на вопросы и искать актуальную инфу\n"
        f"• Слушать голосовые\n"
        f"• Анализировать фото и PDF\n"
        f"• Ставить напоминания → iPhone Calendar\n"
        f"• Генерировать фото /image и видео /video\n"
        f"• Вести темы раздельно /topics\n"
        f"• Очистить историю /reset\n\n"
        f"{tier_info_text(user_id)}"
    )
    if is_new and user_id not in (OWNER_ID, VIP_ID):
        conn = sqlite3.connect("memory.db")
        total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        conn.close()
        try:
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=f"🆕 Новый: {name} {username} ({user_id})\nВсего: {total}"
            )
        except Exception:
            pass

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    topic = get_user_topic(user_id)
    # Сбрасываем все состояния
    context.user_data.clear()
    if topic:
        clear_history(user_id, topic)
        await update.message.reply_text(f"🗑 История темы [{topic.upper()}] очищена. Начнём заново!")
    else:
        clear_history(user_id)
        await update.message.reply_text("🗑 История очищена. Начнём заново!")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(tier_info_text(update.effective_user.id))

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if get_tier(user_id) not in (TIER_OWNER, TIER_VIP):
        return
    args = context.args or []
    if not args or args[0] == "list":
        conn = sqlite3.connect("memory.db")
        rows = conn.execute("SELECT user_id, tier, name, username FROM users ORDER BY tier").fetchall()
        conn.close()
        lines = ["👥 Пользователи:"]
        for uid, t, n, un in rows:
            usage = get_usage(uid)
            lines.append(f"  {TIER_NAMES.get(t, t)}: {n} {un} ({uid}) — {usage[0]} msg")
        await update.message.reply_text("\n".join(lines) or "Пусто")
    elif args[0] == "set" and len(args) >= 3:
        try:
            target = int(args[1])
            new_tier = args[2].lower()
            if new_tier not in TIER_LIMITS:
                await update.message.reply_text(f"Тиры: {', '.join(TIER_LIMITS)}")
                return
            set_tier(target, new_tier)
            await update.message.reply_text(f"✅ {target} → {TIER_NAMES[new_tier]}")
        except ValueError:
            await update.message.reply_text("Неверный ID")
    else:
        await update.message.reply_text(
            "/admin list\n/admin set <id> <tier>\nТиры: owner vip subscriber beta blocked"
        )

async def cmd_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if get_tier(user_id) == TIER_BLOCKED:
        await update.message.reply_text("❌ Доступ закрыт.")
        return
    if not check_limit(user_id, "images"):
        tier = get_tier(user_id)
        limit = TIER_LIMITS[tier][1]
        await update.message.reply_text(f"⚠️ Лимит фото исчерпан ({limit}/день). Обновится в полночь.")
        return
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Текст → Фото", callback_data="media:img_t2i")],
        [InlineKeyboardButton("🖼 Фото → Фото (редактирование)", callback_data="media:img_i2i")],
    ])
    await update.message.reply_text("🎨 Выбери режим:", reply_markup=keyboard)

async def cmd_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if get_tier(user_id) == TIER_BLOCKED:
        await update.message.reply_text("❌ Доступ закрыт.")
        return
    if not check_limit(user_id, "videos"):
        tier = get_tier(user_id)
        limit = TIER_LIMITS[tier][2]
        await update.message.reply_text(f"⚠️ Лимит видео исчерпан ({limit}/день). Обновится в полночь.")
        return
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Текст → Видео", callback_data="media:vid_t2v")],
        [InlineKeyboardButton("🎬 Фото → Видео", callback_data="media:vid_i2v")],
    ])
    await update.message.reply_text("🎬 Выбери режим:", reply_markup=keyboard)

async def cmd_topics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    topics = get_user_topics(user_id)
    current = get_user_topic(user_id) or "авто"
    keyboard = []
    for name in topics:
        marker = "✅ " if name == current else ""
        keyboard.append([InlineKeyboardButton(f"{marker}{name.upper()}", callback_data=f"topic:{name}")])
    keyboard.append([InlineKeyboardButton("🔄 Авто-определение", callback_data="topic:auto")])
    keyboard.append([InlineKeyboardButton("➕ Новая тема", callback_data="topic:new")])
    await update.message.reply_text(
        f"Темы (активна: *{current}*)\nКаждая тема — отдельная история:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

# ══════════════════════════════════════════════════════════════════════════════
# CALLBACKS
# ══════════════════════════════════════════════════════════════════════════════
async def topics_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    data = query.data
    if data == "topic:auto":
        set_user_topic(user_id, None)
        await query.edit_message_text("🔄 Тема определяется автоматически.")
    elif data == "topic:new":
        context.user_data["waiting_new_topic"] = True
        await query.edit_message_text(
            "Напиши название темы (и описание через запятую):\n_работа с клиентом, задачи по проекту_",
            parse_mode="Markdown"
        )
    elif data.startswith("topic:"):
        topic = data.split(":", 1)[1]
        set_user_topic(user_id, topic)
        notes = get_topic_notes(user_id, topic)
        notes_text = f"\n\n📝 Заметки:\n{notes}" if notes else ""
        await query.edit_message_text(
            f"✅ Тема: *{topic.upper()}*{notes_text}\n\nИстория этой темы загружена.",
            parse_mode="Markdown"
        )

async def media_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "media:img_t2i":
        context.user_data["media_mode"] = "img_t2i"
        await query.edit_message_text("📝 Напиши промпт для картинки:")

    elif data == "media:img_i2i":
        context.user_data["media_mode"] = "img_i2i"
        await query.edit_message_text(
            "🖼 Напиши что изменить, потом пришли фото.\n"
            "Или сначала пришли фото — спрошу промпт."
        )

    elif data == "media:vid_t2v":
        context.user_data["media_mode"] = "vid_t2v"
        await query.edit_message_text("📝 Напиши промпт для видео:")

    elif data == "media:vid_i2v":
        context.user_data["media_mode"] = "vid_i2v"
        await query.edit_message_text(
            "🎬 Пришли фото. Можно сначала написать промпт — или сразу фото."
        )

# ══════════════════════════════════════════════════════════════════════════════
# HANDLERS
# ══════════════════════════════════════════════════════════════════════════════
async def handle_new_topic_input(update, context) -> bool:
    if not context.user_data.get("waiting_new_topic"):
        return False
    user_id = update.effective_user.id
    text = update.message.text
    parts = [p.strip() for p in text.split(",", 1)]
    name = parts[0].lower()
    description = parts[1] if len(parts) > 1 else ""
    add_user_topic(user_id, name, description)
    set_user_topic(user_id, name)
    context.user_data.pop("waiting_new_topic", None)
    await update.message.reply_text(
        f"✅ Тема *{name.upper()}* создана!\nВеду историю отдельно.",
        parse_mode="Markdown"
    )
    return True

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text
    try:
        if await handle_new_topic_input(update, context):
            return

        # ── Старые кнопки ReplyKeyboard (от прошлой версии бота) ──────────
        OLD_BTN_MAP = {
            "🎨 генерация фото": cmd_image,
            "🎬 генерация видео": cmd_video,
            "📚 темы": cmd_topics,
            "💎 подписка": cmd_status,
            "❓ помощь": cmd_status,
        }
        tl_btn = user_text.lower().strip()
        for btn_text, handler in OLD_BTN_MAP.items():
            if tl_btn == btn_text:
                await handler(update, context)
                return

        # ── Состояния "ждём промпт к фото/видео (photo-first)" ────────────
        if context.user_data.get("waiting_img_edit_photo_first"):
            file_id = context.user_data.get("waiting_img_edit_photo_first")
            prompt = user_text if user_text.lower() != "без промпта" else "improve this image"
            if not check_limit(user_id, "images"):
                context.user_data.pop("waiting_img_edit_photo_first", None)
                await update.message.reply_text("⚠️ Лимит фото исчерпан.")
                return
            await update.message.reply_text("🎨 Редактирую...")
            direct_url = await upload_file_id_to_tmpfiles(context, file_id)
            if not direct_url:
                context.user_data.pop("waiting_img_edit_photo_first", None)
                await update.message.reply_text("❌ Не удалось загрузить фото. Пришли снова.")
                return
            url = await asyncio.to_thread(generate_image_edit_ws, direct_url, prompt)
            if url:
                context.user_data.pop("waiting_img_edit_photo_first", None)
                inc_usage(user_id, "images")
                await context.bot.send_photo(chat_id=update.effective_chat.id, photo=url,
                                              caption=f"✅ Промпт: {prompt[:80]}")
            else:
                # Не очищаем стейт — пусть пользователь попробует другой промпт
                await update.message.reply_text("❌ Не удалось. Попробуй изменить промпт:")
            return

        if context.user_data.get("waiting_video_photo_first"):
            file_id = context.user_data.get("waiting_video_photo_first")
            prompt = "" if user_text.lower() == "без промпта" else user_text
            if not check_limit(user_id, "videos"):
                context.user_data.pop("waiting_video_photo_first", None)
                await update.message.reply_text("⚠️ Лимит видео исчерпан.")
                return
            await update.message.reply_text("🎬 Генерирую видео (30-60 сек)...")
            direct_url = await upload_file_id_to_tmpfiles(context, file_id)
            if not direct_url:
                context.user_data.pop("waiting_video_photo_first", None)
                await update.message.reply_text("❌ Не удалось загрузить фото. Пришли снова.")
                return
            url = await asyncio.to_thread(generate_video_ws, direct_url, prompt)
            if url:
                context.user_data.pop("waiting_video_photo_first", None)
                inc_usage(user_id, "videos")
                await context.bot.send_video(
                    chat_id=update.effective_chat.id, video=url,
                    caption="✅ Готово!" + (f" Промпт: {prompt}" if prompt else "")
                )
            else:
                # Сохраняем фото, меняем только промпт
                await update.message.reply_text("❌ Не удалось. Попробуй другой промпт:")
            return

        # ── Медиа-режим: ждём промпт ──────────────────────────────────────
        mode = context.user_data.get("media_mode")

        if mode == "img_t2i":
            if not check_limit(user_id, "images"):
                context.user_data.pop("media_mode", None)
                await update.message.reply_text("⚠️ Лимит фото исчерпан.")
                return
            await update.message.reply_text("🎨 Генерирую...")
            url = await asyncio.to_thread(generate_image_ws, user_text)
            if url:
                context.user_data.pop("media_mode", None)
                inc_usage(user_id, "images")
                await context.bot.send_photo(chat_id=update.effective_chat.id, photo=url,
                                              caption=f"✅ Промпт: {user_text[:80]}")
            else:
                # Оставляем mode=img_t2i, пусть попробует снова
                await update.message.reply_text("❌ Не удалось. Напиши промпт ещё раз:")
            return

        elif mode == "img_i2i":
            context.user_data["img_edit_prompt"] = user_text
            context.user_data["waiting_img_edit"] = True
            context.user_data.pop("media_mode", None)
            await update.message.reply_text(f"✅ Промпт: «{user_text}»\nТеперь пришли фото.")
            return

        elif mode == "vid_t2v":
            if not check_limit(user_id, "videos"):
                context.user_data.pop("media_mode", None)
                await update.message.reply_text("⚠️ Лимит видео исчерпан.")
                return
            await update.message.reply_text("🎬 Генерирую видео (30-60 сек)...")
            url = await asyncio.to_thread(generate_video_from_text_ws, user_text)
            if url:
                context.user_data.pop("media_mode", None)
                inc_usage(user_id, "videos")
                await context.bot.send_video(chat_id=update.effective_chat.id, video=url,
                                              caption=f"✅ Промпт: {user_text[:80]}")
            else:
                await update.message.reply_text("❌ Не удалось. Напиши промпт ещё раз:")
            return

        elif mode == "vid_i2v":
            context.user_data["video_prompt"] = user_text
            context.user_data["waiting_video"] = True
            context.user_data.pop("media_mode", None)
            await update.message.reply_text(f"✅ Промпт: «{user_text}»\nТеперь пришли фото.")
            return

        # ── Перехват запросов на генерацию ────────────────────────────────
        tl = user_text.lower()
        if any(w in tl for w in ["сгенерируй фото", "сгенерируй картинку", "нарисуй",
                                   "создай фото", "создай картинку", "сделай фото"]):
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📝 Текст → Фото", callback_data="media:img_t2i")],
                [InlineKeyboardButton("🖼 Фото → Фото", callback_data="media:img_i2i")],
            ])
            await update.message.reply_text("🎨 Выбери режим:", reply_markup=keyboard)
            return
        if any(w in tl for w in ["сгенерируй видео", "создай видео", "сделай видео"]):
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📝 Текст → Видео", callback_data="media:vid_t2v")],
                [InlineKeyboardButton("🎬 Фото → Видео", callback_data="media:vid_i2v")],
            ])
            await update.message.reply_text("🎬 Выбери режим:", reply_markup=keyboard)
            return

        # ── Обычный чат ───────────────────────────────────────────────────
        if not await check_access(update, "messages"):
            return

        # Напоминания
        if await asyncio.to_thread(detect_reminder, user_text):
            dt, title = await asyncio.to_thread(parse_reminder, user_text)
            if dt and title:
                await send_reminder_ics(update, context, title, dt, user_id)
            else:
                await update.message.reply_text(
                    "Не смог разобрать дату.\nПример: напомни 15 июля в 10:00 встреча с клиентом"
                )
            return

        # Предпочтения
        if any(w in tl for w in ["запомни что", "запомни:", "всегда отвечай", "никогда не пиши"]):
            save_prefs(user_id, (get_prefs(user_id) + "\n" + user_text).strip())
            await update.message.reply_text("✅ Запомнил!")
            return

        # Тема
        pinned = get_user_topic(user_id)
        topic = pinned if pinned else await asyncio.to_thread(detect_topic, user_text, user_id)

        # Поиск
        search_ctx = ""
        if await asyncio.to_thread(needs_search, user_text):
            await update.message.reply_text("🔍 Ищу...")
            search_ctx = await asyncio.to_thread(web_search, user_text)

        save_message(user_id, "user", user_text, topic)
        await update_summary(user_id)
        history = get_history(user_id, topic)
        if search_ctx:
            history[-1]["content"] += f"\n\n[Поиск]:\n{search_ctx}"

        resp = await ai_create(
            model="claude-sonnet-4-6", max_tokens=1200,
            stop_sequences=["user:", "\nuser:", "\nUser:"],
            system=get_system(user_id, topic), messages=history
        )
        reply = resp.content[0].text.strip()
        save_message(user_id, "assistant", reply, topic)
        await maybe_update_notes(user_id, topic, history)

        label = f"[{topic.upper()}] " if topic and topic != "общее" else ""
        await update.message.reply_text(f"{label}{reply}")

    except Exception as e:
        logger.error(f"Text error: {e}")
        await update.message.reply_text("⚠️ Ошибка. Попробуй ещё раз или /reset")

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        if not await check_access(update, "messages"):
            return
        await update.message.reply_text("🎙 Транскрибирую...")
        voice = update.message.voice
        file = await context.bot.get_file(voice.file_id)
        path = f"/tmp/voice_{user_id}.ogg"
        await file.download_to_drive(path)
        segments, _ = await asyncio.to_thread(whisper.transcribe, path, language="ru")
        text = " ".join([s.text for s in segments]).strip()
        os.remove(path)
        if not text:
            await update.message.reply_text("Не смог распознать.")
            return
        await update.message.reply_text(f"📝 Распознал: {text}")

        if await asyncio.to_thread(detect_reminder, text):
            dt, title = await asyncio.to_thread(parse_reminder, text)
            if dt and title:
                await send_reminder_ics(update, context, title, dt, user_id)
            return

        pinned = get_user_topic(user_id)
        topic = pinned if pinned else await asyncio.to_thread(detect_topic, text, user_id)
        save_message(user_id, "user", text, topic)
        history = get_history(user_id, topic)
        resp = await ai_create(
            model="claude-sonnet-4-6", max_tokens=1200,
            system=get_system(user_id, topic), messages=history
        )
        reply = resp.content[0].text
        save_message(user_id, "assistant", reply, topic)
        label = f"[{topic.upper()}] " if topic and topic != "общее" else ""
        await update.message.reply_text(f"{label}{reply}")
    except Exception as e:
        logger.error(f"Voice: {e}")
        await update.message.reply_text("⚠️ Ошибка с голосовым.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        # ── Ждём фото для img_i2i (промпт уже есть) ───────────────────────
        if context.user_data.get("waiting_img_edit"):
            prompt = context.user_data.get("img_edit_prompt", "improve this image")
            if not check_limit(user_id, "images"):
                context.user_data.pop("waiting_img_edit", None)
                context.user_data.pop("img_edit_prompt", None)
                await update.message.reply_text("⚠️ Лимит фото исчерпан.")
                return
            await update.message.reply_text("🎨 Редактирую...")
            direct_url = await upload_to_tmpfiles(context, update.message.photo[-1])
            if not direct_url:
                await update.message.reply_text("❌ Не удалось загрузить фото. Пришли снова.")
                return
            url = await asyncio.to_thread(generate_image_edit_ws, direct_url, prompt)
            if url:
                context.user_data.pop("waiting_img_edit", None)
                context.user_data.pop("img_edit_prompt", None)
                inc_usage(user_id, "images")
                await context.bot.send_photo(chat_id=update.effective_chat.id, photo=url,
                                              caption=f"✅ Промпт: {prompt[:80]}")
            else:
                # Оставляем стейт, пусть пришлёт фото снова
                await update.message.reply_text("❌ Не удалось. Пришли фото ещё раз:")
            return

        # ── Ждём фото для i2v (промпт уже есть) ──────────────────────────
        if context.user_data.get("waiting_video"):
            prompt = context.user_data.get("video_prompt", "")
            if not check_limit(user_id, "videos"):
                context.user_data.pop("waiting_video", None)
                context.user_data.pop("video_prompt", None)
                await update.message.reply_text("⚠️ Лимит видео исчерпан.")
                return
            await update.message.reply_text("🎬 Генерирую видео (30-60 сек)...")
            direct_url = await upload_to_tmpfiles(context, update.message.photo[-1])
            if not direct_url:
                await update.message.reply_text("❌ Не удалось загрузить фото. Пришли снова.")
                return
            url = await asyncio.to_thread(generate_video_ws, direct_url, prompt)
            if url:
                context.user_data.pop("waiting_video", None)
                context.user_data.pop("video_prompt", None)
                inc_usage(user_id, "videos")
                await context.bot.send_video(
                    chat_id=update.effective_chat.id, video=url,
                    caption="✅ Готово!" + (f" Промпт: {prompt}" if prompt else "")
                )
            else:
                await update.message.reply_text("❌ Не удалось. Пришли фото ещё раз:")
            return

        # ── img_i2i: фото пришло первым ───────────────────────────────────
        if context.user_data.get("media_mode") == "img_i2i":
            context.user_data.pop("media_mode", None)
            caption_prompt = update.message.caption
            if caption_prompt:
                # Есть подпись — используем как промпт, генерим сразу
                if not check_limit(user_id, "images"):
                    await update.message.reply_text("⚠️ Лимит фото исчерпан.")
                    return
                await update.message.reply_text("🎨 Редактирую...")
                direct_url = await upload_to_tmpfiles(context, update.message.photo[-1])
                if not direct_url:
                    await update.message.reply_text("❌ Не удалось загрузить фото.")
                    return
                url = await asyncio.to_thread(generate_image_edit_ws, direct_url, caption_prompt)
                if url:
                    inc_usage(user_id, "images")
                    await context.bot.send_photo(chat_id=update.effective_chat.id, photo=url,
                                                  caption=f"✅ Промпт: {caption_prompt[:80]}")
                else:
                    # Сохраняем фото для повторной попытки
                    context.user_data["waiting_img_edit_photo_first"] = update.message.photo[-1].file_id
                    await update.message.reply_text("❌ Не удалось. Попробуй другой промпт:")
            else:
                # Нет подписи — ждём промпт текстом
                context.user_data["waiting_img_edit_photo_first"] = update.message.photo[-1].file_id
                await update.message.reply_text("Что сделать с фото? Напиши промпт:")
            return

        # ── vid_i2v: фото пришло первым, ждём промпт ─────────────────────
        if context.user_data.get("media_mode") == "vid_i2v":
            context.user_data.pop("media_mode", None)
            context.user_data["waiting_video_photo_first"] = update.message.photo[-1].file_id
            await update.message.reply_text("Опиши движение для видео (или 'без промпта'):")
            return

        # ── Авто-триггер i2i: фото + запрос генерации в caption ──────────
        photo_caption = update.message.caption or ""
        GEN_KEYWORDS = ["сгенерируй", "нарисуй", "создай картинку", "создай фото",
                        "сделай картинку", "на основе", "в стиле мультяш", "мультяшн"]
        if any(w in photo_caption.lower() for w in GEN_KEYWORDS):
            tier = get_tier(user_id)
            if tier not in (TIER_BETA, TIER_BLOCKED) or TIER_LIMITS[tier][1] > 0:
                if not check_limit(user_id, "images"):
                    await update.message.reply_text("⚠️ Лимит фото исчерпан.")
                    return
                await update.message.reply_text("🎨 Редактирую на основе фото...")
                direct_url = await upload_to_tmpfiles(context, update.message.photo[-1])
                if not direct_url:
                    await update.message.reply_text("❌ Не удалось загрузить фото.")
                    return
                url = await asyncio.to_thread(generate_image_edit_ws, direct_url, photo_caption)
                if url:
                    inc_usage(user_id, "images")
                    await context.bot.send_photo(chat_id=update.effective_chat.id, photo=url,
                                                  caption=f"✅ {photo_caption[:80]}")
                else:
                    context.user_data["waiting_img_edit_photo_first"] = update.message.photo[-1].file_id
                    await update.message.reply_text("❌ Не удалось. Попробуй изменить промпт:")
                return

        # ── Анализ фото через Claude Vision ──────────────────────────────
        if not await check_access(update, "messages"):
            return
        await update.message.reply_text("🔍 Анализирую фото...")
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        file_bytes = await file.download_as_bytearray()
        image_data = base64.standard_b64encode(file_bytes).decode("utf-8")
        caption = update.message.caption or "Что на этом фото?"
        pinned = get_user_topic(user_id)
        topic = pinned if pinned else await asyncio.to_thread(detect_topic, caption, user_id)
        history = get_history(user_id, topic)
        messages = history + [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data}},
            {"type": "text", "text": caption}
        ]}]
        resp = await ai_create(model="claude-sonnet-4-6", max_tokens=1200,
                                   stop_sequences=["user:", "\nuser:", "\nUser:"],
                                   system=get_system(user_id, topic), messages=messages)
        reply = resp.content[0].text.strip()
        # Сохраняем нейтральный текст, не '[фото]' чтобы не путать будущие запросы
        save_message(user_id, "user", f"пользователь прислал фото: {caption}", topic)
        save_message(user_id, "assistant", reply, topic)
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"Photo: {e}")
        await update.message.reply_text("⚠️ Ошибка с фото.")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        if not await check_access(update, "messages"):
            return
        doc = update.message.document
        caption = update.message.caption or ""
        pinned = get_user_topic(user_id)
        topic = pinned if pinned else "общее"
        if doc.mime_type == "application/pdf":
            await update.message.reply_text("📄 Читаю PDF...")
            file = await context.bot.get_file(doc.file_id)
            file_bytes = await file.download_as_bytearray()
            pdf_data = base64.standard_b64encode(file_bytes).decode("utf-8")
            messages = [{"role": "user", "content": [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_data}},
                {"type": "text", "text": caption or "Проанализируй документ."}
            ]}]
        elif doc.mime_type and doc.mime_type.startswith("image/"):
            await update.message.reply_text("🔍 Анализирую изображение...")
            file = await context.bot.get_file(doc.file_id)
            file_bytes = await file.download_as_bytearray()
            image_data = base64.standard_b64encode(file_bytes).decode("utf-8")
            history = get_history(user_id, topic)
            messages = history + [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": doc.mime_type, "data": image_data}},
                {"type": "text", "text": caption or "Что на этом изображении?"}
            ]}]
        else:
            await update.message.reply_text("Умею читать PDF и изображения.")
            return
        resp = await ai_create(model="claude-sonnet-4-6", max_tokens=1200,
                                   stop_sequences=["user:", "\nuser:", "\nUser:"],
                                   system=get_system(user_id, topic), messages=messages)
        reply = resp.content[0].text.strip()
        save_message(user_id, "user", f"пользователь прислал файл: {caption}", topic)
        save_message(user_id, "assistant", reply, topic)
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"Document: {e}")
        await update.message.reply_text("⚠️ Ошибка с файлом.")

async def check_reminders(context):
    for rid, user_id, title in get_due_reminders():
        try:
            await context.bot.send_message(chat_id=user_id, text=f"⏰ Напоминание: {title}")
            mark_reminder_fired(rid)
        except Exception as e:
            logger.error(f"Reminder: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# BOOT
# ══════════════════════════════════════════════════════════════════════════════
init_db()
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.job_queue.run_repeating(check_reminders, interval=60, first=10)

async def on_error(update, context):
    logger.error("Unhandled error", exc_info=context.error)

app.add_error_handler(on_error)

app.add_handler(CommandHandler("start",  cmd_start))
app.add_handler(CommandHandler("reset",  cmd_reset))
app.add_handler(CommandHandler("image",  cmd_image))
app.add_handler(CommandHandler("video",  cmd_video))
app.add_handler(CommandHandler("status", cmd_status))
app.add_handler(CommandHandler("admin",  cmd_admin))
app.add_handler(CommandHandler("topics", cmd_topics))
app.add_handler(CallbackQueryHandler(topics_callback, pattern="^topic:"))
app.add_handler(CallbackQueryHandler(media_callback,  pattern="^media:"))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
app.add_handler(MessageHandler(filters.VOICE, handle_voice))
app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

print("BX Bot v8 — clean rewrite")
app.run_polling()
