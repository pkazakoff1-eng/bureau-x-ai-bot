from dotenv import load_dotenv
load_dotenv()

import anthropic
import sqlite3
import base64
import os
import subprocess
import logging
from datetime import datetime, timezone, timedelta
import asyncio
import httpx
from telegram import Update, LabeledPrice, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import (ApplicationBuilder, MessageHandler, CommandHandler,
                          PreCheckoutQueryHandler, ConversationHandler,
                          CallbackQueryHandler, filters, ContextTypes)
from faster_whisper import WhisperModel
from tavily import TavilyClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_KEY", "")
TAVILY_KEY = os.getenv("TAVILY_KEY", "")

ADMIN_IDS = {285198612, 587290278}
WAVESPEED_KEY = os.getenv("WAVESPEED_API_KEY", "")

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("🎨 Генерация фото"), KeyboardButton("🎬 Генерация видео")],
        [KeyboardButton("📂 Темы"), KeyboardButton("💳 Подписка"), KeyboardButton("❓ Помощь")],
    ],
    resize_keyboard=True,
    input_field_placeholder="Напиши сообщение или выбери действие..."
)

# Conversation states
IMG_PHOTO, IMG_PROMPT, IMG_SETTINGS = range(3)
VID_PHOTO, VID_PROMPT = range(3, 5)
TRIAL_DAYS = 5
MAX_USERS = 5  # лимит тестировщиков
STARS_PRICE = 200       # Stars за 30 дней (~$2)
SUB_DAYS = 30

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
tavily = TavilyClient(api_key=TAVILY_KEY)
whisper = WhisperModel("tiny", device="cpu", compute_type="int8")

def init_db():
    conn = sqlite3.connect('memory.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS messages
                 (user_id INTEGER, role TEXT, content TEXT,
                  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS preferences
                 (user_id INTEGER PRIMARY KEY, prefs TEXT DEFAULT '')''')
    c.execute('''CREATE TABLE IF NOT EXISTS summaries
                 (user_id INTEGER PRIMARY KEY, summary TEXT DEFAULT '',
                  updated DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        status TEXT DEFAULT 'trial',
        trial_start DATETIME DEFAULT CURRENT_TIMESTAMP,
        expires_at DATETIME DEFAULT NULL,
        username TEXT DEFAULT '',
        image_count INTEGER DEFAULT 0
    )''')
    # migrate: add image_count if missing
    try:
        c.execute('ALTER TABLE users ADD COLUMN image_count INTEGER DEFAULT 0')
    except:
        pass
    conn.commit()
    conn.close()

def get_total_users():
    conn = sqlite3.connect('memory.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users WHERE user_id NOT IN ({})".format(
        ','.join(str(i) for i in ADMIN_IDS)))
    count = c.fetchone()[0]
    conn.close()
    return count

def register_user(user_id, username=""):
    conn = sqlite3.connect('memory.db')
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    conn.commit()
    conn.close()

def get_user_status(user_id):
    conn = sqlite3.connect('memory.db')
    c = conn.cursor()
    c.execute("SELECT status, trial_start, expires_at FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row

def activate_subscription(user_id):
    conn = sqlite3.connect('memory.db')
    c = conn.cursor()
    now = datetime.now(timezone.utc)
    # Если уже есть активная подписка — продлеваем от текущего expires_at
    c.execute("SELECT expires_at FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if row and row[0]:
        try:
            current = datetime.fromisoformat(row[0])
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            base = max(now, current)
        except:
            base = now
    else:
        base = now
    new_expires = base + timedelta(days=SUB_DAYS)
    c.execute("UPDATE users SET status='active', expires_at=? WHERE user_id=?",
              (new_expires.isoformat(), user_id))
    conn.commit()
    conn.close()
    return new_expires

def is_allowed(user_id):
    if user_id in ADMIN_IDS:
        return True, None
    row = get_user_status(user_id)
    if not row:
        return False, "not_registered"
    status, trial_start, expires_at = row
    if status == "blocked":
        return False, "blocked"
    if status == "active":
        if expires_at:
            exp = datetime.fromisoformat(expires_at)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > exp:
                return False, "expired"
        return True, None
    # trial
    start = datetime.fromisoformat(trial_start)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    days_passed = (datetime.now(timezone.utc) - start).days
    if days_passed >= TRIAL_DAYS:
        return False, "expired"
    days_left = TRIAL_DAYS - days_passed
    return True, f"trial_{days_left}"

def get_history(user_id):
    conn = sqlite3.connect('memory.db')
    c = conn.cursor()
    c.execute('SELECT role, content FROM messages WHERE user_id = ? ORDER BY timestamp DESC LIMIT 50', (user_id,))
    rows = c.fetchall()
    conn.close()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

def get_message_count(user_id):
    conn = sqlite3.connect('memory.db')
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM messages WHERE user_id = ?', (user_id,))
    count = c.fetchone()[0]
    conn.close()
    return count

def save_message(user_id, role, content):
    conn = sqlite3.connect('memory.db')
    c = conn.cursor()
    c.execute('INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)', (user_id, role, content))
    conn.commit()
    conn.close()

def get_summary(user_id):
    conn = sqlite3.connect('memory.db')
    c = conn.cursor()
    c.execute('SELECT summary FROM summaries WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else ''

def save_summary(user_id, summary):
    conn = sqlite3.connect('memory.db')
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO summaries (user_id, summary, updated) VALUES (?, ?, CURRENT_TIMESTAMP)', (user_id, summary))
    conn.commit()
    conn.close()

def get_prefs(user_id):
    conn = sqlite3.connect('memory.db')
    c = conn.cursor()
    c.execute('SELECT prefs FROM preferences WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else ''

def save_prefs(user_id, prefs):
    conn = sqlite3.connect('memory.db')
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO preferences (user_id, prefs) VALUES (?, ?)', (user_id, prefs))
    conn.commit()
    conn.close()

def needs_search(text):
    keywords = ['найди', 'поищи', 'что сейчас', 'актуально', 'последние', 'новости',
                'цена', 'стоимость', 'требования', 'правила', 'как сейчас', 'узнай', 'проверь']
    return any(word in text.lower() for word in keywords)

def web_search(query):
    try:
        result = tavily.search(query=query, max_results=3)
        texts = [r['content'] for r in result['results']]
        return "\n\n".join(texts[:3])
    except Exception as e:
        logger.error(f"Search error: {e}")
        return ""

async def update_summary(user_id):
    count = get_message_count(user_id)
    if count % 30 == 0 and count > 0:
        history = get_history(user_id)
        old_summary = get_summary(user_id)
        messages = [{"role": "user", "content": f"Сделай краткое резюме этого диалога в 3-5 предложениях. Предыдущее резюме: {old_summary}\n\nНовые сообщения:\n" + "\n".join([f"{m['role']}: {m['content']}" for m in history])}]
        response = client.messages.create(model="claude-sonnet-4-6", max_tokens=500, messages=messages)
        save_summary(user_id, response.content[0].text)

def get_system(user_id):
    prefs = get_prefs(user_id)
    summary = get_summary(user_id)
    if user_id in ADMIN_IDS:
        system = """Ты личный семейный ассистент и проджект-менеджер.

Автоматически определяй категорию каждого сообщения:
- РАБОТА: задачи по видео, монтажу, правкам, клиентам, РМГ, Shot Films
- ИСПАНИЯ: всё про переезд, жизнь в Испании, недвижимость, визы, животные
- ТВОРЧЕСКОЕ: контент для инсты, видео промпты, идеи, AI-генерация
- ЛИЧНОЕ: семья, быт, здоровье, личные дела

В начале ответа пиши категорию в скобках, например (ИСПАНИЯ).
Ты умеешь анализировать фото и документы.
Если в контексте есть результаты поиска — используй их для ответа.
Отвечай кратко и по делу."""
    else:
        system = """Ты умный персональный ассистент.
Ты умеешь отвечать на вопросы, анализировать фото и документы, искать информацию в интернете.
Отвечай кратко и по делу."""

    if summary:
        system += f"\n\nРезюме предыдущих разговоров:\n{summary}"
    if prefs:
        system += f"\n\nПредпочтения пользователя:\n{prefs}"
    return system



def get_image_count(user_id):
    conn = sqlite3.connect('memory.db')
    c = conn.cursor()
    c.execute("SELECT image_count FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

def increment_image_count(user_id):
    conn = sqlite3.connect('memory.db')
    c = conn.cursor()
    c.execute("UPDATE users SET image_count = image_count + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def can_generate_image(user_id):
    """Admins: unlimited. Active sub: unlimited. Trial/free: 1 free image."""
    if user_id in ADMIN_IDS:
        return True, None
    row = get_user_status(user_id)
    if not row:
        return False, "not_registered"
    status, _, expires_at = row
    if status == "active":
        if expires_at:
            from datetime import datetime, timezone
            exp = datetime.fromisoformat(expires_at)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > exp:
                return False, "no_sub"
        return True, None
    # trial or expired — 1 free image
    count = get_image_count(user_id)
    if count < 3:
        return True, "free_image"
    return False, "no_sub"

def can_generate_video(user_id):
    """Admins: unlimited. Active sub only."""
    if user_id in ADMIN_IDS:
        return True, None
    row = get_user_status(user_id)
    if not row:
        return False, "not_registered"
    status, _, expires_at = row
    if status == "active":
        if expires_at:
            from datetime import datetime, timezone
            exp = datetime.fromisoformat(expires_at)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > exp:
                return False, "no_sub"
        return True, None
    return False, "no_sub"

async def wavespeed_request(endpoint: str, payload: dict) -> str:
    """POST to Wavespeed, poll until done, return output URL."""
    headers = {
        "Authorization": f"Bearer {WAVESPEED_KEY}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"https://api.wavespeed.ai/api/v3/{endpoint}",
            json=payload, headers=headers
        )
        data = resp.json()
        request_id = data.get("data", {}).get("id")
        if not request_id:
            raise Exception(f"API error: {data}")
        for _ in range(90):
            await asyncio.sleep(2)
            poll = await client.get(
                f"https://api.wavespeed.ai/api/v3/predictions/{request_id}",
                headers=headers
            )
            result = poll.json()
            status = result.get("data", {}).get("status")
            if status == "completed":
                outputs = result["data"].get("outputs", [])
                if outputs:
                    return outputs[0]
                raise Exception("No outputs in response")
            elif status in ("failed", "canceled"):
                raise Exception(result.get("data", {}).get("error", "Unknown error"))
        raise Exception("Generation timeout")



# ── Topics ────────────────────────────────────────────────────────────────────

CREATE_TOPIC = 10

async def cmd_topics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    user_id = update.effective_user.id
    topics = get_topics(user_id)
    active_id, active_name = get_active_topic(context)

    keyboard = []
    for tid, name in topics:
        marker = "✅ " if tid == active_id else ""
        keyboard.append([
            InlineKeyboardButton(f"{marker}{name}", callback_data=f"topic_select_{tid}_{name}"),
            InlineKeyboardButton("🗑", callback_data=f"topic_delete_{tid}")
        ])
    keyboard.append([InlineKeyboardButton("+ Создать тему", callback_data="topic_create")])
    if active_id:
        keyboard.append([InlineKeyboardButton("❌ Выйти из темы", callback_data="topic_exit")])

    current = f"Сейчас: *{active_name}*" if active_name else "Сейчас: общий чат"
    await update.message.reply_text(
        f"📂 *Мои темы*\n{current}\n\nВыбери тему или создай новую:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def topics_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data

    if data == "topic_create":
        await query.edit_message_text("Напиши название новой темы:")
        context.user_data['waiting_topic_name'] = True
        return

    if data == "topic_exit":
        clear_active_topic(context)
        await query.edit_message_text("Вышел из темы. Теперь общий чат.")
        return

    if data.startswith("topic_select_"):
        parts = data.split("_", 3)
        tid = int(parts[2])
        name = parts[3]
        set_active_topic(context, tid, name)
        await query.edit_message_text(f"✅ Тема: *{name}*\n\nПиши — отвечу в контексте этой темы.", parse_mode="Markdown")
        return

    if data.startswith("topic_delete_"):
        tid = int(data.split("_")[2])
        active_id, _ = get_active_topic(context)
        if active_id == tid:
            clear_active_topic(context)
        delete_topic(tid, user_id)
        topics = get_topics(user_id)
        keyboard = []
        for t_id, name in topics:
            keyboard.append([
                InlineKeyboardButton(name, callback_data=f"topic_select_{t_id}_{name}"),
                InlineKeyboardButton("🗑", callback_data=f"topic_delete_{t_id}")
            ])
        keyboard.append([InlineKeyboardButton("+ Создать тему", callback_data="topic_create")])
        await query.edit_message_text(
            "🗑 Тема удалена.\n\nВыбери тему:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# ── /image conversation ────────────────────────────────────────────────────────

async def cmd_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return ConversationHandler.END
    context.user_data.pop("img_ref", None)
    context.user_data.pop("prompt", None)
    keyboard = [[InlineKeyboardButton("⏭ Пропустить", callback_data="img_skip_photo")]]
    await update.message.reply_text(
        "🎨 *Генерация изображения*\n\n"
        "Шаг 1: Прикрепи фото как референс.\n"
        "Или нажми Пропустить чтобы генерировать с нуля.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return IMG_PHOTO

async def img_receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        file_bytes = await file.download_as_bytearray()
        context.user_data["img_ref"] = base64.standard_b64encode(file_bytes).decode()
        await update.message.reply_text("✅ Фото получил!\n\nШаг 2: Напиши промт — что именно сгенерировать?")
        return IMG_PROMPT
    await update.message.reply_text("Отправь фото или нажми Пропустить.")
    return IMG_PHOTO

async def img_skip_photo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Шаг 2: Напиши промт — что сгенерировать?")
    return IMG_PROMPT

async def img_receive_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text:
        return IMG_PROMPT
    context.user_data["prompt"] = update.message.text
    keyboard = [[
        InlineKeyboardButton("1:1 📷", callback_data="ratio_1:1"),
        InlineKeyboardButton("16:9 🖥", callback_data="ratio_16:9"),
        InlineKeyboardButton("9:16 📱", callback_data="ratio_9:16"),
    ]]
    await update.message.reply_text(
        f"✅ Промт: _{update.message.text[:80]}_\n\nШаг 3: Выбери формат:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return IMG_SETTINGS

async def img_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query.data.startswith("ratio_"):
        return
    await query.answer()
    user_id = update.effective_user.id
    allowed, reason = can_generate_image(user_id)
    if not allowed:
        await query.edit_message_text(
            "🔒 Генерация изображений доступна по подписке.\n"
            f"Оформи за {STARS_PRICE} ⭐️/месяц — /subscribe"
        )
        return ConversationHandler.END
    ratio = query.data.replace("ratio_", "")
    free_notice = " (бесплатная попытка)" if reason == "free_image" else ""
    await query.edit_message_text(f"⏳ Генерирую ({ratio}){free_notice}...")

    prompt = context.user_data.get("prompt", "")
    img_ref = context.user_data.get("img_ref")
    try:
        if img_ref:
            payload = {
                "prompt": prompt,
                "image": f"data:image/jpeg;base64,{img_ref}",
                "aspect_ratio": ratio,
                "strength": 0.75,
                "num_inference_steps": 28,
                "guidance_scale": 3.5,
            }
            url = await wavespeed_request("wavespeed-ai/flux-dev", payload)
        else:
            payload = {
                "prompt": prompt,
                "aspect_ratio": ratio,
                "num_inference_steps": 4,
            }
            url = await wavespeed_request("wavespeed-ai/flux-schnell", payload)
        increment_image_count(user_id)
        await query.message.reply_photo(photo=url, caption=f"✅ {prompt[:200]}")
    except Exception as e:
        logger.error(f"Image gen error: {e}")
        await query.message.reply_text(f"Ошибка генерации: {e}")
    return ConversationHandler.END


# ── /video conversation ────────────────────────────────────────────────────────

async def cmd_video_gen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text(
        "🎬 *Генерация видео*\n\nОтправь фото, из которого сделаем видео.",
        parse_mode="Markdown"
    )
    return VID_PHOTO

async def vid_receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Нужно фото. Отправь изображение.")
        return VID_PHOTO
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    file_bytes = await file.download_as_bytearray()
    context.user_data["vid_photo"] = base64.standard_b64encode(file_bytes).decode()
    if update.message.caption:
        context.user_data["vid_prompt"] = update.message.caption
        return await vid_generate(update, context)
    await update.message.reply_text(
        "Фото получил! Напиши промт — что должно происходить в видео.\n"
        "Или /skip для автоматического движения."
    )
    return VID_PROMPT

async def vid_receive_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text and update.message.text.strip() == "/skip":
        context.user_data["vid_prompt"] = "Smooth cinematic motion, high quality"
    else:
        context.user_data["vid_prompt"] = update.message.text or ""
    return await vid_generate(update, context)

async def vid_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    allowed, reason = can_generate_video(user_id)
    if not allowed:
        await update.message.reply_text(
            "🔒 Генерация видео доступна только по подписке.\n"
            f"Оформи за {STARS_PRICE} ⭐️/месяц — /subscribe"
        )
        return ConversationHandler.END
    await update.message.reply_text("⏳ Генерирую видео, ~30-60 сек...")
    photo_b64 = context.user_data.get("vid_photo", "")
    prompt = context.user_data.get("vid_prompt", "Smooth cinematic motion")
    try:
        payload = {
            "image": f"data:image/jpeg;base64,{photo_b64}",
            "prompt": prompt,
            "duration": 5,
            "resolution": "480p",
        }
        url = await wavespeed_request("bytedance/seedance-2-0-mini-i2v-480p", payload)
        await update.message.reply_video(video=url, caption=f"✅ {prompt[:200]}")
    except Exception as e:
        logger.error(f"Video gen error: {e}")
        await update.message.reply_text(f"Ошибка генерации видео: {e}")
    return ConversationHandler.END

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END

init_db()

async def check_expiring_subscriptions(context: ContextTypes.DEFAULT_TYPE):
    """Runs daily — notifies users 3 days before expiry and on expiry day."""
    conn = sqlite3.connect('memory.db')
    c = conn.cursor()
    now = datetime.now(timezone.utc)

    # Expiring in ~3 days
    in_3_days = now + timedelta(days=3)
    c.execute("""
        SELECT user_id FROM users
        WHERE status = 'active'
        AND expires_at IS NOT NULL
        AND datetime(expires_at) BETWEEN datetime(?) AND datetime(?)
    """, (now.isoformat(), in_3_days.isoformat()))
    expiring_soon = c.fetchall()

    # Already expired (within last 24h)
    yesterday = now - timedelta(days=1)
    c.execute("""
        SELECT user_id FROM users
        WHERE status = 'active'
        AND expires_at IS NOT NULL
        AND datetime(expires_at) < datetime(?)
        AND datetime(expires_at) > datetime(?)
    """, (now.isoformat(), yesterday.isoformat()))
    just_expired = c.fetchall()
    conn.close()

    for (user_id,) in expiring_soon:
        if user_id in ADMIN_IDS:
            continue
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"⏰ Подписка заканчивается через 3 дня.\n\n"
                     f"Продли сейчас чтобы не прерываться — /subscribe"
            )
        except Exception as e:
            logger.error(f"Notify error {user_id}: {e}")

    for (user_id,) in just_expired:
        if user_id in ADMIN_IDS:
            continue
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"😔 Подписка закончилась.\n\n"
                     f"Оформи снова — {STARS_PRICE} ⭐️/мес: /subscribe\n\n"
                     f"Или оставь фидбек: @BX_Supp_bot"
            )
            # Update status to trial so check_access handles them correctly
            conn2 = sqlite3.connect('memory.db')
            c2 = conn2.cursor()
            c2.execute("UPDATE users SET status='trial' WHERE user_id=?", (user_id,))
            conn2.commit()
            conn2.close()
        except Exception as e:
            logger.error(f"Expired notify error {user_id}: {e}")


def init_topics(conn):
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS topics (
        topic_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        name TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS topic_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        topic_id INTEGER,
        role TEXT,
        content TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()

def get_topics(user_id):
    conn = sqlite3.connect('memory.db')
    init_topics(conn)
    c = conn.cursor()
    c.execute("SELECT topic_id, name FROM topics WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def create_topic(user_id, name):
    conn = sqlite3.connect('memory.db')
    init_topics(conn)
    c = conn.cursor()
    c.execute("INSERT INTO topics (user_id, name) VALUES (?, ?)", (user_id, name))
    topic_id = c.lastrowid
    conn.commit()
    conn.close()
    return topic_id

def delete_topic(topic_id, user_id):
    conn = sqlite3.connect('memory.db')
    c = conn.cursor()
    c.execute("DELETE FROM topics WHERE topic_id = ? AND user_id = ?", (topic_id, user_id))
    c.execute("DELETE FROM topic_messages WHERE topic_id = ? AND user_id = ?", (topic_id, user_id))
    conn.commit()
    conn.close()

def get_topic_history(user_id, topic_id):
    conn = sqlite3.connect('memory.db')
    c = conn.cursor()
    c.execute("SELECT role, content FROM topic_messages WHERE user_id = ? AND topic_id = ? ORDER BY timestamp DESC LIMIT 50",
              (user_id, topic_id))
    rows = c.fetchall()
    conn.close()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

def save_topic_message(user_id, topic_id, role, content):
    conn = sqlite3.connect('memory.db')
    init_topics(conn)
    c = conn.cursor()
    c.execute("INSERT INTO topic_messages (user_id, topic_id, role, content) VALUES (?, ?, ?, ?)",
              (user_id, topic_id, role, content))
    conn.commit()
    conn.close()

def get_active_topic(context):
    return context.user_data.get('active_topic_id'), context.user_data.get('active_topic_name')

def set_active_topic(context, topic_id, name):
    context.user_data['active_topic_id'] = topic_id
    context.user_data['active_topic_name'] = name

def clear_active_topic(context):
    context.user_data.pop('active_topic_id', None)
    context.user_data.pop('active_topic_name', None)


async def send_subscribe_invoice(update: Update):
    """Отправить инвойс на оплату Stars"""
    await update.message.reply_invoice(
        title="Подписка на 30 дней",
        description="Полный доступ к AI-ассистенту на 30 дней",
        payload="subscribe_30",
        currency="XTR",
        prices=[LabeledPrice("30 дней доступа", STARS_PRICE)],
    )

GIF_FILE_ID = None
GIF_PATH = "/root/tg_bot/bx_logo.mp4"

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global GIF_FILE_ID
    user_id = update.effective_user.id
    username = update.effective_user.username or ""
    # Проверяем лимит до регистрации (если юзер уже есть — пропускаем)
    if user_id not in ADMIN_IDS:
        row = get_user_status(user_id)
        if not row and get_total_users() >= MAX_USERS:
            await update.message.reply_text(
                "Извини, набор закрыт — все места заняты 🙏\n\n"
                "Напишу когда откроется следующий поток."
            )
            return
    register_user(user_id, username)
    if user_id in ADMIN_IDS:
        caption = "Привет! Я твой личный ассистент. Готов к работе 🚀"
    else:
        caption = (
            "Привет! Я персональный AI-ассистент.\n\n"
            f"У тебя есть {TRIAL_DAYS} дней бесплатного доступа ко всем функциям.\n\n"
        "Просто пиши — отвечу, расшифрую голосовые и видео, разберу фото и PDF, найду в интернете.\n"
        "Для генерации используй команды:\n"
        "/image — сгенерировать изображение\n"
        "/video — создать видео из фото\n"
    
            f"После пробного периода — подписка {STARS_PRICE} ⭐️ в месяц."
        )
    try:
        reply_markup = MAIN_KEYBOARD
        if GIF_FILE_ID:
            msg = await update.message.reply_animation(animation=GIF_FILE_ID, caption=caption, reply_markup=reply_markup)
        else:
            with open(GIF_PATH, "rb") as gif_f:
                msg = await update.message.reply_animation(animation=gif_f, caption=caption, reply_markup=reply_markup)
            if msg.animation:
                GIF_FILE_ID = msg.animation.file_id
                logger.info(f"GIF file_id cached: {GIF_FILE_ID}")
    except Exception as e:
        logger.error(f"GIF send error: {e}")
        await update.message.reply_text(caption, reply_markup=reply_markup)

async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in ADMIN_IDS:
        await update.message.reply_text("У тебя безлимитный доступ 😎")
        return
    register_user(user_id, update.effective_user.username or "")
    await send_subscribe_invoice(update)

async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    expires = activate_subscription(user_id)
    exp_str = expires.strftime("%d.%m.%Y")
    await update.message.reply_text(
        f"✅ Оплата прошла! Доступ открыт до {exp_str}.\n\n"
        f"Спасибо! Пиши мне в любое время 🚀"
    )

async def check_access(update: Update) -> bool:
    user_id = update.effective_user.id
    allowed, reason = is_allowed(user_id)
    if allowed:
        return True
    if reason == "not_registered":
        await update.message.reply_text("Напиши /start чтобы начать.")
    elif reason == "expired":
        await update.message.reply_text(
            f"Твой пробный период закончился 😔\n\n"
            f"Оформи подписку — {STARS_PRICE} ⭐️ в месяц:\n\n"
            "Или оставь фидбек: @BX_Supp_bot"
        )
        await send_subscribe_invoice(update)
    elif reason == "blocked":
        await update.message.reply_text("Доступ ограничен.")
    return False

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    user_id = update.effective_user.id
    user_text = update.message.text
    # Handle keyboard buttons
    if context.user_data.get('waiting_topic_name'):
        del context.user_data['waiting_topic_name']
        tid = create_topic(user_id, user_text)
        set_active_topic(context, tid, user_text)
        await update.message.reply_text(f"✅ Тема *{user_text}* создана и выбрана!", parse_mode="Markdown")
        return
    if user_text == "📂 Темы":
        await cmd_topics(update, context)
        return
    if user_text == "🎨 Генерация фото":
        await cmd_image(update, context)
        return
    if user_text == "🎬 Генерация видео":
        await cmd_video_gen(update, context)
        return
    if user_text == "💳 Подписка":
        await cmd_subscribe(update, context)
        return
    if user_text == "❓ Помощь":
        await update.message.reply_text(
            "Просто напиши мне что угодно — отвечу.\n\n"
            "Также умею:\n"
            "🎤 Голосовые и кружочки — расшифрую\n"
            "📷 Фото и PDF — проанализирую\n"
            "🌐 Веб-поиск — найду актуальное\n"
            "🎨 /image — генерация изображения\n"
            "🎬 /video — генерация видео из фото\n"
            "💳 /subscribe — подписка"
        )
        return
    try:
        if any(word in user_text.lower() for word in ['запомни что', 'запомни:', 'всегда', 'никогда не']):
            prefs = get_prefs(user_id)
            new_prefs = prefs + '\n' + user_text if prefs else user_text
            save_prefs(user_id, new_prefs)
            await update.message.reply_text("Запомнил!")
            return
        search_context = ""
        if needs_search(user_text):
            await update.message.reply_text("Ищу в интернете...")
            search_context = web_search(user_text)
        active_id, active_name = get_active_topic(context)
        if active_id:
            save_topic_message(user_id, active_id, "user", user_text)
            history = get_topic_history(user_id, active_id)
            if search_context:
                history[-1]["content"] += f"\n\n[Результаты поиска]:\n{search_context}"
            topic_system = get_system(user_id) + f"\n\nСейчас активна тема: {active_name}. Веди диалог в этом контексте."
            response = client.messages.create(
                model="claude-sonnet-4-6", max_tokens=1000,
                system=topic_system, messages=history)
            reply = response.content[0].text
            save_topic_message(user_id, active_id, "assistant", reply)
            await update.message.reply_text(f"[{active_name}] {reply}")
        else:
            save_message(user_id, "user", user_text)
            await update_summary(user_id)
            history = get_history(user_id)
            if search_context:
                history[-1]["content"] += f"\n\n[Результаты поиска]:\n{search_context}"
            response = client.messages.create(
                model="claude-sonnet-4-6", max_tokens=1000,
                system=get_system(user_id), messages=history)
            reply = response.content[0].text
            save_message(user_id, "assistant", reply)
            await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"Text error: {e}")
        await update.message.reply_text("Ошибка, попробуй ещё раз.")

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    user_id = update.effective_user.id
    try:
        await update.message.reply_text("Голосовое получил, транскрибирую...")
        file = await context.bot.get_file(update.message.voice.file_id)
        path = f"/tmp/voice_{user_id}.ogg"
        await file.download_to_drive(path)
        segments, _ = whisper.transcribe(path, language="ru")
        text = " ".join([s.text for s in segments])
        os.remove(path)
        if not text.strip():
            await update.message.reply_text("Не смог распознать.")
            return
        await update.message.reply_text(f"Распознал: {text}")
        save_message(user_id, "user", text)
        response = client.messages.create(model="claude-sonnet-4-6", max_tokens=1000,
                                          system=get_system(user_id), messages=get_history(user_id))
        reply = response.content[0].text
        save_message(user_id, "assistant", reply)
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"Voice error: {e}")
        await update.message.reply_text("Ошибка с голосовым.")

async def extract_video_frames(video_path: str, user_id: int, max_frames: int = 4) -> list:
    """Extract frames from video, return list of base64 strings."""
    frames = []
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True
        )
        duration = float(result.stdout.strip() or "5")
        interval = max(1, duration / max_frames)
        for i in range(min(max_frames, int(duration))):
            t = i * interval
            frame_path = f"/tmp/frame_{user_id}_{i}.jpg"
            subprocess.run(
                ["ffmpeg", "-y", "-ss", str(t), "-i", video_path,
                 "-vframes", "1", "-q:v", "3", "-vf", "scale=640:-1", frame_path],
                capture_output=True
            )
            if os.path.exists(frame_path):
                with open(frame_path, "rb") as f:
                    frames.append(base64.standard_b64encode(f.read()).decode())
                os.remove(frame_path)
    except Exception as e:
        logger.error(f"Frame extraction error: {e}")
    return frames

async def handle_media_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.animation:
        await handle_animation(update, context)
        return
    if not await check_access(update):
        return
    user_id = update.effective_user.id
    is_circle = update.message.video_note is not None
    try:
        if is_circle:
            await update.message.reply_text("Кружочек получил, анализирую...")
        else:
            await update.message.reply_text("Видео получил, извлекаю звук...")
        video = update.message.video_note or update.message.video
        if not video:
            await update.message.reply_text("Не понял тип видео.")
            return
        file = await context.bot.get_file(video.file_id)
        video_path = f"/tmp/media_{user_id}.mp4"
        audio_path = f"/tmp/media_{user_id}.wav"
        await file.download_to_drive(video_path)

        # Extract audio
        audio_result = subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-vn", "-ar", "16000", "-ac", "1", audio_path],
            capture_output=True, text=True)
        speech_text = ""
        if audio_result.returncode == 0 and os.path.exists(audio_path):
            segments, _ = whisper.transcribe(audio_path, language="ru")
            speech_text = " ".join([s.text for s in segments]).strip()
            os.remove(audio_path)

        # For video notes (circles) — also extract frames for visual analysis
        visual_content = []
        if is_circle:
            frames = await extract_video_frames(video_path, user_id, max_frames=3)
            for frame_b64 in frames:
                visual_content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": frame_b64}
                })

        os.remove(video_path)

        caption = update.message.caption or ""

        if is_circle and visual_content:
            # Full analysis: visual + speech
            parts = visual_content.copy()
            prompt = ""
            if speech_text:
                prompt += f"Речь в кружочке: {speech_text}\n\n"
            prompt += caption or "Опиши что происходит в этом кружочке: кто там, что делает, что говорит, какая обстановка."
            parts.append({"type": "text", "text": prompt})

            messages = get_history(user_id) + [{"role": "user", "content": parts}]
            response = client.messages.create(
                model="claude-sonnet-4-6", max_tokens=1000,
                system=get_system(user_id), messages=messages)
            reply = response.content[0].text
            if speech_text:
                save_message(user_id, "user", f"[кружочек] {speech_text}")
            else:
                save_message(user_id, "user", "[кружочек без речи]")
            save_message(user_id, "assistant", reply)
            await update.message.reply_text(reply)
        elif speech_text:
            await update.message.reply_text(f"Распознал: {speech_text}")
            save_message(user_id, "user", speech_text)
            response = client.messages.create(model="claude-sonnet-4-6", max_tokens=1000,
                                              system=get_system(user_id), messages=get_history(user_id))
            reply = response.content[0].text
            save_message(user_id, "assistant", reply)
            await update.message.reply_text(reply)
        else:
            await update.message.reply_text("Не смог распознать речь в видео.")
    except Exception as e:
        logger.error(f"Media video error: {e}", exc_info=True)
        await update.message.reply_text("Ошибка с видео.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        await update.message.reply_text("Фото получил, анализирую...")
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        file_bytes = await file.download_as_bytearray()
        image_data = base64.standard_b64encode(file_bytes).decode('utf-8')
        caption = update.message.caption or "Опиши подробно что на этом фото. Только анализ изображения, без генерации."
        messages = get_history(user_id) + [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data}},
            {"type": "text", "text": caption}]}]
        response = client.messages.create(model="claude-sonnet-4-6", max_tokens=1500,
                                          system=get_system(user_id), messages=messages)
        reply = response.content[0].text
        save_message(user_id, "user", f"[фото] {caption}")
        save_message(user_id, "assistant", reply)
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"Photo error: {e}")
        await update.message.reply_text("Ошибка с фото.")

GEN_KEYWORDS = [
    'сгенерируй', 'сгенери', 'создай', 'нарисуй', 'generate', 'создайте',
    'сделай картинку', 'сделай изображение', 'сделай фото', '360', 'арт',
    'в стиле', 'превратись', 'превrati', 'render', 'draw', 'image of',
    'создай персонажа', 'сделай персонажа', 'аниме', 'cartoon', 'мультяш'
]

async def handle_smart_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    caption = (update.message.caption or "").lower()
    # Check if caption has generation intent
    if any(kw in caption for kw in GEN_KEYWORDS):
        user_id = update.effective_user.id
        allowed, reason = can_generate_image(user_id)
        if not allowed:
            await update.message.reply_text(
                "🔒 Генерация по подписке.\n"
                f"Оформи за {STARS_PRICE} ⭐️/мес — /subscribe"
            )
            return
        # Use photo as reference, caption as prompt
        await update.message.reply_text("🎨 Понял, генерирую по твоей фотке...")
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        file_bytes = await file.download_as_bytearray()
        img_ref = base64.standard_b64encode(file_bytes).decode()
        prompt = update.message.caption or "Create a stylized image based on this photo"
        # Ask for aspect ratio
        keyboard = [[
            InlineKeyboardButton("1:1 📷", callback_data="ratio_1:1"),
            InlineKeyboardButton("16:9 🖥", callback_data="ratio_16:9"),
            InlineKeyboardButton("9:16 📱", callback_data="ratio_9:16"),
        ]]
        context.user_data["prompt"] = prompt
        context.user_data["img_ref"] = img_ref
        await update.message.reply_text("Выбери формат:", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    await handle_photo(update, context)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    user_id = update.effective_user.id
    try:
        doc = update.message.document
        caption = update.message.caption or ""
        if doc.mime_type == 'application/pdf':
            await update.message.reply_text("PDF получил, читаю...")
            file = await context.bot.get_file(doc.file_id)
            file_bytes = await file.download_as_bytearray()
            pdf_data = base64.standard_b64encode(file_bytes).decode('utf-8')
            messages = [{"role": "user", "content": [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_data}},
                {"type": "text", "text": caption or "Проанализируй этот документ подробно."}]}]
        elif doc.mime_type in ('image/gif', 'video/mp4') or (doc.file_name and doc.file_name.endswith('.gif')):
            if user_id in ADMIN_IDS:
                await update.message.reply_text(f"file_id: {doc.file_id}")
            else:
                await update.message.reply_text("GIF получил!")
            return
        elif doc.mime_type and doc.mime_type.startswith('image/'):
            await update.message.reply_text("Изображение получил, анализирую...")
            file = await context.bot.get_file(doc.file_id)
            file_bytes = await file.download_as_bytearray()
            image_data = base64.standard_b64encode(file_bytes).decode('utf-8')
            messages = get_history(user_id) + [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": doc.mime_type, "data": image_data}},
                {"type": "text", "text": caption or "Что на этом изображении?"}]}]
        else:
            await update.message.reply_text("Пока умею читать только PDF и изображения.")
            return
        response = client.messages.create(model="claude-sonnet-4-6", max_tokens=1500,
                                          system=get_system(user_id), messages=messages)
        reply = response.content[0].text
        save_message(user_id, "user", f"[файл] {caption}")
        save_message(user_id, "assistant", reply)
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"Document error: {e}")
        await update.message.reply_text("Ошибка с файлом.")

async def handle_animation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    anim = update.message.animation
    if anim and user_id in ADMIN_IDS:
        await update.message.reply_text(f"file_id: {anim.file_id}")

async def cmd_getid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    msg = update.message.reply_to_message
    if not msg:
        await update.message.reply_text("Ответь на сообщение с медиа командой /getid")
        return
    for field in ['animation', 'video', 'document', 'photo', 'voice']:
        obj = getattr(msg, field, None)
        if obj:
            if field == 'photo':
                fid = obj[-1].file_id
            else:
                fid = obj.file_id
            await update.message.reply_text(f"{field}_file_id:\n`{fid}`", parse_mode='Markdown')
            return
    await update.message.reply_text("Медиа не найдено")

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

# Conversations first (priority)
img_conv = ConversationHandler(
    entry_points=[CommandHandler("image", cmd_image)],
    states={
        IMG_PHOTO: [
            MessageHandler(filters.PHOTO, img_receive_photo),
            CallbackQueryHandler(img_skip_photo_callback, pattern="^img_skip_photo$"),
        ],
        IMG_PROMPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, img_receive_prompt)],
        IMG_SETTINGS: [CallbackQueryHandler(img_settings_callback, pattern="^ratio_")],
    },
    fallbacks=[CommandHandler("cancel", cmd_cancel)],
    per_user=True, per_chat=True,
)
vid_conv = ConversationHandler(
    entry_points=[CommandHandler("video", cmd_video_gen)],
    states={
        VID_PHOTO: [MessageHandler(filters.PHOTO, vid_receive_photo)],
        VID_PROMPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, vid_receive_prompt)],
    },
    fallbacks=[CommandHandler("cancel", cmd_cancel)],
    per_user=True, per_chat=True,
)
app.add_handler(img_conv)
app.add_handler(vid_conv)

app.add_handler(CommandHandler("topics", cmd_topics))
app.add_handler(CallbackQueryHandler(topics_callback, pattern="^topic_"))
app.add_handler(CommandHandler("start", cmd_start))
app.add_handler(CommandHandler("subscribe", cmd_subscribe))
app.add_handler(CommandHandler("getid", cmd_getid))
app.add_handler(PreCheckoutQueryHandler(pre_checkout))
app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
app.add_handler(MessageHandler(filters.VOICE, handle_voice))
app.add_handler(MessageHandler(filters.ANIMATION, handle_animation))
app.add_handler(MessageHandler((filters.VIDEO & ~filters.ANIMATION) | filters.VIDEO_NOTE, handle_media_video))
app.add_handler(MessageHandler(filters.PHOTO, handle_smart_photo))
app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
print("Бот запущен v8 — уведомления подписки")
# Schedule daily subscription check at 10:00 UTC
import datetime as dt
app.job_queue.run_daily(
    check_expiring_subscriptions,
    time=dt.time(hour=10, minute=0, tzinfo=timezone.utc)
)
app.run_polling()
