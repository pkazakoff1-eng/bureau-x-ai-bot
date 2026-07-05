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

# ── Strings / i18n ────────────────────────────────────────────────────────────
STRINGS = {
    'ru': {
        'btn_image': '🎨 Генерация фото',
        'btn_video': '🎬 Генерация видео',
        'btn_topics': '📂 Темы',
        'btn_sub': '💳 Подписка',
        'btn_help': '❓ Помощь',
        'btn_lang': '🌐 Язык',
        'kb_placeholder': 'Напиши сообщение или выбери действие...',
        'start_admin': 'Привет! Я твой личный ассистент. Готов к работе 🚀',
        'start_user': (
            'Привет! Я персональный AI-ассистент.\n\n'
            'У тебя есть {days} дней бесплатного доступа ко всем функциям.\n\n'
            'Просто пиши — отвечу, расшифрую голосовые и видео, разберу фото и PDF, найду в интернете.\n'
            'Для генерации используй команды:\n'
            '/image — сгенерировать изображение\n'
            '/video — создать видео из фото\n\n'
            'После пробного периода — подписка {price} ⭐️ в месяц.'
        ),
        'start_closed': 'Извини, набор закрыт — все места заняты 🙏\n\nНапишу когда откроется следующий поток.',
        'help_text': (
            'Просто напиши мне что угодно — отвечу.\n\n'
            'Также умею:\n'
            '🎤 Голосовые и кружочки — расшифрую\n'
            '📷 Фото и PDF — проанализирую\n'
            '🌐 Веб-поиск — найду актуальное\n'
            '🎨 /image — генерация изображения\n'
            '🎬 /video — генерация видео из фото\n'
            '💳 /subscribe — подписка'
        ),
        'access_not_registered': 'Напиши /start чтобы начать.',
        'access_expired': 'Твой пробный период закончился 😔\n\nОформи подписку — {price} ⭐️ в месяц:\n\nИли оставь фидбек: @BX_Supp_bot',
        'access_blocked': 'Доступ ограничен.',
        'sub_admin': 'У тебя безлимитный доступ 😎',
        'sub_title': 'Подписка на 30 дней',
        'sub_desc': 'Полный доступ к AI-ассистенту на 30 дней',
        'sub_label': '30 дней доступа',
        'payment_ok': '✅ Оплата прошла! Доступ открыт до {date}.\n\nСпасибо! Пиши мне в любое время 🚀',
        'img_start': '🎨 *Генерация изображения*\n\nШаг 1: Прикрепи фото как референс.\nИли нажми Пропустить, чтобы генерировать с нуля.',
        'img_skip_btn': '⏭ Пропустить',
        'img_got_photo': '✅ Фото получил!\n\nШаг 2: Напиши промт — что именно сгенерировать?',
        'img_need_photo': 'Отправь фото или нажми Пропустить.',
        'img_skip_msg': 'Шаг 2: Напиши промт — что сгенерировать?',
        'img_choose_ratio': '✅ Промт: _{prompt}_\n\nШаг 3: Выбери формат:',
        'img_no_sub': '🔒 Генерация изображений доступна по подписке.\nОформи за {price} ⭐️/месяц — /subscribe',
        'img_generating': '⏳ Генерирую ({ratio}){notice}...',
        'img_free_notice': ' (бесплатная попытка)',
        'vid_start': '🎬 *Генерация видео*\n\nОтправь фото — из него сделаем видео.',
        'vid_need_photo': 'Нужно фото. Отправь изображение.',
        'vid_got_photo': '📸 Фото получил!\n\nНапиши промт — что должно происходить в видео.\nИли отправь /skip для автоматического движения.',
        'vid_settings': '⚙️ Выбери параметры видео:',
        'vid_no_sub': '🔒 Генерация видео доступна только по подписке.\nОформи за {price} ⭐️/месяц — /subscribe',
        'vid_generating': '⏳ Генерирую видео {dur} сек / {res}, ~30-90 сек...',
        'cancelled': 'Отменено.',
        'topics_header': '📂 *Мои темы*\n{current}\n\nВыбери тему или создай новую:',
        'topics_current': 'Сейчас: *{name}*',
        'topics_general': 'Сейчас: общий чат',
        'topics_type_name': 'Напиши название новой темы:',
        'topics_exited': 'Вышел из темы. Теперь общий чат.',
        'topics_create_btn': '+ Создать тему',
        'topics_exit_btn': '❌ Выйти из темы',
        'topics_selected': '✅ Тема: *{name}*\n\nПиши — отвечу в контексте этой темы.',
        'topics_deleted': '🗑 Тема удалена.\n\nВыбери тему:',
        'topics_created': '✅ Тема *{name}* создана и выбрана!',
        'topics_ask_goal': '✅ Тема: *{name}*\n\nЧто хочешь сделать в этой теме? Напиши цель или вопрос — сразу отвечу по делу.\nИли просто пиши — войду в контекст темы.',
        'topics_goal_set': '[{name}] Понял, работаем. Пиши.',
        'topics_save_as': '💡 Сохранить этот разговор как тему?',
        'topics_save_btn': '💾 Сохранить как тему',
        'topics_saved': '✅ Тема *{name}* создана!',
        'topics_detect_prompt': 'Определи одним коротким словом или фразой (максимум 3 слова) тему этого разговора на основе последних сообщений. Только тема, без пояснений.',
        'voice_received': 'Голосовое получил, транскрибирую...',
        'voice_no_speech': 'Не смог распознать.',
        'voice_recognized': 'Распознал: {text}',
        'voice_error': 'Ошибка с голосовым.',
        'photo_received': 'Фото получил, анализирую...',
        'photo_caption': 'Опиши подробно что на этом фото. Только анализ изображения, без генерации.',
        'photo_error': 'Ошибка с фото.',
        'gen_photo_confirm': '🎨 Понял, генерирую по твоей фотке...',
        'gen_choose_ratio': 'Выбери формат:',
        'gen_no_sub': '🔒 Генерация по подписке.\nОформи за {price} ⭐️/мес — /subscribe',
        'video_circle_received': 'Кружочек получил, анализирую...',
        'video_received': 'Видео получил, извлекаю звук...',
        'video_unknown': 'Не понял тип видео.',
        'video_no_speech': 'Не смог распознать речь в видео.',
        'video_error': 'Ошибка с видео.',
        'circle_prompt': 'Опиши что происходит в этом кружочке: кто там, что делает, что говорит, какая обстановка.',
        'doc_pdf_received': 'PDF получил, читаю...',
        'doc_pdf_prompt': 'Проанализируй этот документ подробно.',
        'doc_img_received': 'Изображение получил, анализирую...',
        'doc_img_question': 'Что на этом изображении?',
        'doc_unsupported': 'Пока умею читать только PDF и изображения.',
        'doc_error': 'Ошибка с файлом.',
        'remembered': 'Запомнил!',
        'searching': 'Ищу в интернете...',
        'error_retry': 'Ошибка, попробуй ещё раз.',
        'lang_choose': '🌐 Выбери язык / Choose language:',
        'lang_set_ru': '✅ Язык установлен: Русский 🇷🇺',
        'lang_set_en': '✅ Language set: English 🇬🇧',
        'notify_expiring': '⏰ Подписка заканчивается через 3 дня.\n\nПродли сейчас чтобы не прерываться — /subscribe',
        'notify_expired': '😔 Подписка закончилась.\n\nОформи снова — {price} ⭐️/мес: /subscribe\n\nИли оставь фидбек: @BX_Supp_bot',
        'search_keywords': ['найди', 'поищи', 'что сейчас', 'актуально', 'последние', 'новости',
                            'цена', 'стоимость', 'требования', 'правила', 'как сейчас', 'узнай', 'проверь'],
        'remember_keywords': ['запомни что', 'запомни:', 'всегда', 'никогда не'],
    },
    'en': {
        'btn_image': '🎨 Generate image',
        'btn_video': '🎬 Generate video',
        'btn_topics': '📂 Topics',
        'btn_sub': '💳 Subscription',
        'btn_help': '❓ Help',
        'btn_lang': '🌐 Language',
        'kb_placeholder': 'Type a message or choose an action...',
        'start_admin': 'Hi! I\'m your personal assistant. Ready to go 🚀',
        'start_user': (
            'Hi! I\'m your personal AI assistant.\n\n'
            'You have {days} days of free access to all features.\n\n'
            'Just write — I\'ll reply, transcribe voice and video, analyze photos and PDFs, search the web.\n'
            'For generation use commands:\n'
            '/image — generate an image\n'
            '/video — create video from photo\n\n'
            'After the trial — subscription {price} ⭐️/month.'
        ),
        'start_closed': 'Sorry, registration is closed — all spots are taken 🙏\n\nI\'ll notify you when the next wave opens.',
        'help_text': (
            'Just write me anything — I\'ll respond.\n\n'
            'I can also:\n'
            '🎤 Voice & video notes — transcribe\n'
            '📷 Photos & PDFs — analyze\n'
            '🌐 Web search — find current info\n'
            '🎨 /image — generate an image\n'
            '🎬 /video — generate video from photo\n'
            '💳 /subscribe — subscription'
        ),
        'access_not_registered': 'Send /start to begin.',
        'access_expired': 'Your trial period is over 😔\n\nGet a subscription — {price} ⭐️/month:\n\nOr leave feedback: @BX_Supp_bot',
        'access_blocked': 'Access restricted.',
        'sub_admin': 'You have unlimited access 😎',
        'sub_title': '30-day subscription',
        'sub_desc': 'Full access to the AI assistant for 30 days',
        'sub_label': '30 days access',
        'payment_ok': '✅ Payment received! Access open until {date}.\n\nThank you! Write to me anytime 🚀',
        'img_start': '🎨 *Image generation*\n\nStep 1: Attach a reference photo.\nOr tap Skip to generate from scratch.',
        'img_skip_btn': '⏭ Skip',
        'img_got_photo': '✅ Photo received!\n\nStep 2: Write a prompt — what should be generated?',
        'img_need_photo': 'Send a photo or tap Skip.',
        'img_skip_msg': 'Step 2: Write a prompt — what to generate?',
        'img_choose_ratio': '✅ Prompt: _{prompt}_\n\nStep 3: Choose format:',
        'img_no_sub': '🔒 Image generation requires a subscription.\nGet one for {price} ⭐️/month — /subscribe',
        'img_generating': '⏳ Generating ({ratio}){notice}...',
        'img_free_notice': ' (free attempt)',
        'vid_start': '🎬 *Video generation*\n\nSend a photo — we\'ll turn it into a video.',
        'vid_need_photo': 'A photo is required. Send an image.',
        'vid_got_photo': '📸 Photo received!\n\nWrite a prompt — what should happen in the video.\nOr send /skip for automatic motion.',
        'vid_settings': '⚙️ Choose video settings:',
        'vid_no_sub': '🔒 Video generation requires a subscription.\nGet one for {price} ⭐️/month — /subscribe',
        'vid_generating': '⏳ Generating video {dur}s / {res}, ~30-90s...',
        'cancelled': 'Cancelled.',
        'topics_header': '📂 *My topics*\n{current}\n\nSelect a topic or create a new one:',
        'topics_current': 'Now: *{name}*',
        'topics_general': 'Now: general chat',
        'topics_type_name': 'Enter the new topic name:',
        'topics_exited': 'Exited topic. Back to general chat.',
        'topics_create_btn': '+ Create topic',
        'topics_exit_btn': '❌ Exit topic',
        'topics_selected': '✅ Topic: *{name}*\n\nWrite — I\'ll reply in this topic\'s context.',
        'topics_deleted': '🗑 Topic deleted.\n\nSelect a topic:',
        'topics_created': '✅ Topic *{name}* created and selected!',
        'topics_ask_goal': '✅ Topic: *{name}*\n\nWhat do you want to do in this topic? Write your goal or question — I\'ll focus on it right away.\nOr just write — I\'ll pick up the context.',
        'topics_goal_set': '[{name}] Got it. Go ahead.',
        'topics_save_as': '💡 Save this conversation as a topic?',
        'topics_save_btn': '💾 Save as topic',
        'topics_saved': '✅ Topic *{name}* created!',
        'topics_detect_prompt': 'Identify in one short phrase (max 3 words) the topic of this conversation based on the last messages. Only the topic, no explanation.',
        'voice_received': 'Voice message received, transcribing...',
        'voice_no_speech': 'Could not recognize speech.',
        'voice_recognized': 'Recognized: {text}',
        'voice_error': 'Error processing voice message.',
        'photo_received': 'Photo received, analyzing...',
        'photo_caption': 'Describe in detail what is in this photo. Analysis only, no generation.',
        'photo_error': 'Error processing photo.',
        'gen_photo_confirm': '🎨 Got it, generating from your photo...',
        'gen_choose_ratio': 'Choose format:',
        'gen_no_sub': '🔒 Generation requires a subscription.\nGet one for {price} ⭐️/month — /subscribe',
        'video_circle_received': 'Video note received, analyzing...',
        'video_received': 'Video received, extracting audio...',
        'video_unknown': 'Unknown video type.',
        'video_no_speech': 'Could not recognize speech in the video.',
        'video_error': 'Error processing video.',
        'circle_prompt': 'Describe what is happening in this video note: who is there, what they are doing, what they say, the setting.',
        'doc_pdf_received': 'PDF received, reading...',
        'doc_pdf_prompt': 'Analyze this document in detail.',
        'doc_img_received': 'Image received, analyzing...',
        'doc_img_question': 'What is in this image?',
        'doc_unsupported': 'I can only read PDFs and images for now.',
        'doc_error': 'Error processing file.',
        'remembered': 'Got it, remembered!',
        'searching': 'Searching the web...',
        'error_retry': 'Error, please try again.',
        'lang_choose': '🌐 Выбери язык / Choose language:',
        'lang_set_ru': '✅ Язык установлен: Русский 🇷🇺',
        'lang_set_en': '✅ Language set: English 🇬🇧',
        'notify_expiring': '⏰ Your subscription expires in 3 days.\n\nRenew now to avoid interruptions — /subscribe',
        'notify_expired': '😔 Your subscription has expired.\n\nRenew — {price} ⭐️/month: /subscribe\n\nOr leave feedback: @BX_Supp_bot',
        'search_keywords': ['find', 'search', 'look up', 'current', 'latest', 'news',
                            'price', 'cost', 'requirements', 'rules', 'how now', 'check'],
        'remember_keywords': ['remember that', 'remember:', 'always', 'never'],
    }
}

def t(lang: str, key: str, **kwargs) -> str:
    """Get translated string."""
    s = STRINGS.get(lang, STRINGS['ru']).get(key, STRINGS['ru'].get(key, key))
    if kwargs:
        try:
            s = s.format(**kwargs)
        except Exception:
            pass
    return s

def make_keyboard(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(t(lang, 'btn_image')), KeyboardButton(t(lang, 'btn_video'))],
            [KeyboardButton(t(lang, 'btn_topics')), KeyboardButton(t(lang, 'btn_sub'))],
            [KeyboardButton(t(lang, 'btn_help')), KeyboardButton(t(lang, 'btn_lang'))],
        ],
        resize_keyboard=True,
        input_field_placeholder=t(lang, 'kb_placeholder')
    )

# Conversation states
IMG_PHOTO, IMG_PROMPT, IMG_SETTINGS = range(3)
VID_PHOTO, VID_PROMPT, VID_SETTINGS = range(3, 6)
TRIAL_DAYS = 5
MAX_USERS = 5
STARS_PRICE = 200
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
        image_count INTEGER DEFAULT 0,
        language TEXT DEFAULT 'ru'
    )''')
    for col, default in [('image_count', '0'), ('language', "'ru'"), ('expires_at', 'NULL')]:
        try:
            c.execute(f'ALTER TABLE users ADD COLUMN {col} {"INTEGER" if col == "image_count" else "TEXT"} DEFAULT {default}')
        except:
            pass
    conn.commit()
    conn.close()

def get_lang(user_id: int) -> str:
    conn = sqlite3.connect('memory.db')
    c = conn.cursor()
    c.execute("SELECT language FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return (row[0] or 'ru') if row else 'ru'

def set_lang(user_id: int, lang: str):
    conn = sqlite3.connect('memory.db')
    c = conn.cursor()
    c.execute("UPDATE users SET language = ? WHERE user_id = ?", (lang, user_id))
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

def needs_search(text, lang='ru'):
    keywords = STRINGS[lang].get('search_keywords', STRINGS['ru']['search_keywords'])
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
    lang = get_lang(user_id)
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
    elif lang == 'en':
        system = """You are a smart personal assistant.
You can answer questions, analyze photos and documents, and search the internet.
Reply concisely and to the point. Always respond in English."""
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
    if user_id in ADMIN_IDS:
        return True, None
    row = get_user_status(user_id)
    if not row:
        return False, "not_registered"
    status, _, expires_at = row
    if status == "active":
        if expires_at:
            exp = datetime.fromisoformat(expires_at)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > exp:
                return False, "no_sub"
        return True, None
    count = get_image_count(user_id)
    if count < 3:
        return True, "free_image"
    return False, "no_sub"

def can_generate_video(user_id):
    if user_id in ADMIN_IDS:
        return True, None
    row = get_user_status(user_id)
    if not row:
        return False, "not_registered"
    status, _, expires_at = row
    if status == "active":
        if expires_at:
            exp = datetime.fromisoformat(expires_at)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > exp:
                return False, "no_sub"
        return True, None
    return False, "no_sub"

async def wavespeed_request(endpoint: str, payload: dict) -> str:
    headers = {
        "Authorization": f"Bearer {WAVESPEED_KEY}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.post(
            f"https://api.wavespeed.ai/api/v3/{endpoint}",
            json=payload, headers=headers
        )
        data = resp.json()
        request_id = data.get("data", {}).get("id")
        if not request_id:
            raise Exception(f"API error: {data}")
        for _ in range(90):
            await asyncio.sleep(2)
            poll = await http.get(
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

# ── /language command ─────────────────────────────────────────────────────────

async def cmd_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🇷🇺 Русский", callback_data="setlang_ru"),
        InlineKeyboardButton("🇬🇧 English", callback_data="setlang_en"),
    ]])
    lang = get_lang(update.effective_user.id)
    await update.message.reply_text(t(lang, 'lang_choose'), reply_markup=keyboard)

async def lang_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    new_lang = query.data.replace("setlang_", "")
    set_lang(user_id, new_lang)
    msg = t(new_lang, 'lang_set_ru') if new_lang == 'ru' else t(new_lang, 'lang_set_en')
    await query.edit_message_text(msg)
    # Send updated keyboard
    await context.bot.send_message(
        chat_id=user_id,
        text="👇",
        reply_markup=make_keyboard(new_lang)
    )

# ── Topics ────────────────────────────────────────────────────────────────────

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

async def cmd_topics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    user_id = update.effective_user.id
    lang = get_lang(user_id)
    topics = get_topics(user_id)
    active_id, active_name = get_active_topic(context)

    keyboard = []
    for tid, name in topics:
        marker = "✅ " if tid == active_id else ""
        keyboard.append([
            InlineKeyboardButton(f"{marker}{name}", callback_data=f"topic_select_{tid}_{name}"),
            InlineKeyboardButton("🗑", callback_data=f"topic_delete_{tid}")
        ])
    keyboard.append([InlineKeyboardButton(t(lang, 'topics_create_btn'), callback_data="topic_create")])
    if active_id:
        keyboard.append([InlineKeyboardButton(t(lang, 'topics_exit_btn'), callback_data="topic_exit")])

    current = t(lang, 'topics_current', name=active_name) if active_name else t(lang, 'topics_general')
    await update.message.reply_text(
        t(lang, 'topics_header', current=current),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def topics_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    lang = get_lang(user_id)
    data = query.data

    if data == "topic_create":
        await query.edit_message_text(t(lang, 'topics_type_name'))
        context.user_data['waiting_topic_name'] = True
        return

    if data == "topic_exit":
        clear_active_topic(context)
        await query.edit_message_text(t(lang, 'topics_exited'))
        return

    if data.startswith("topic_select_"):
        parts = data.split("_", 3)
        tid = int(parts[2])
        name = parts[3]
        set_active_topic(context, tid, name)
        # Ask for goal/context for this topic session
        goal_text = t(lang, 'topics_ask_goal', name=name)
        await query.edit_message_text(goal_text, parse_mode="Markdown")
        context.user_data['topic_awaiting_goal'] = True
        return

    if data.startswith("topic_autosave_"):
        name = data.replace("topic_autosave_", "")
        tid = create_topic(user_id, name)
        set_active_topic(context, tid, name)
        await query.edit_message_text(t(lang, 'topics_saved', name=name), parse_mode="Markdown")
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
        keyboard.append([InlineKeyboardButton(t(lang, 'topics_create_btn'), callback_data="topic_create")])
        await query.edit_message_text(
            t(lang, 'topics_deleted'),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# ── /image conversation ────────────────────────────────────────────────────────

async def cmd_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return ConversationHandler.END
    user_id = update.effective_user.id
    lang = get_lang(user_id)
    context.user_data.pop("img_ref", None)
    context.user_data.pop("prompt", None)
    keyboard = [[InlineKeyboardButton(t(lang, 'img_skip_btn'), callback_data="img_skip_photo")]]
    await update.message.reply_text(
        t(lang, 'img_start'),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return IMG_PHOTO

async def img_receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update.effective_user.id)
    if update.message.photo:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        file_bytes = await file.download_as_bytearray()
        context.user_data["img_ref"] = base64.standard_b64encode(file_bytes).decode()
        await update.message.reply_text(t(lang, 'img_got_photo'))
        return IMG_PROMPT
    await update.message.reply_text(t(lang, 'img_need_photo'))
    return IMG_PHOTO

async def img_skip_photo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = get_lang(query.from_user.id)
    await query.edit_message_text(t(lang, 'img_skip_msg'))
    return IMG_PROMPT

async def img_receive_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text:
        return IMG_PROMPT
    lang = get_lang(update.effective_user.id)
    context.user_data["prompt"] = update.message.text
    keyboard = [[
        InlineKeyboardButton("1:1 📷", callback_data="ratio_1:1"),
        InlineKeyboardButton("16:9 🖥", callback_data="ratio_16:9"),
        InlineKeyboardButton("9:16 📱", callback_data="ratio_9:16"),
    ]]
    await update.message.reply_text(
        t(lang, 'img_choose_ratio', prompt=update.message.text[:80]),
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
    lang = get_lang(user_id)
    allowed, reason = can_generate_image(user_id)
    if not allowed:
        await query.edit_message_text(t(lang, 'img_no_sub', price=STARS_PRICE))
        return ConversationHandler.END
    ratio = query.data.replace("ratio_", "")
    notice = t(lang, 'img_free_notice') if reason == "free_image" else ""
    await query.edit_message_text(t(lang, 'img_generating', ratio=ratio, notice=notice))

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
        await query.message.reply_text(f"Error: {e}")
    return ConversationHandler.END

# ── /video conversation ────────────────────────────────────────────────────────

async def cmd_video_gen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return ConversationHandler.END
    user_id = update.effective_user.id
    lang = get_lang(user_id)
    context.user_data.clear()
    await update.message.reply_text(t(lang, 'vid_start'), parse_mode="Markdown")
    return VID_PHOTO

async def vid_receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update.effective_user.id)
    if not update.message.photo:
        await update.message.reply_text(t(lang, 'vid_need_photo'))
        return VID_PHOTO
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    file_bytes = await file.download_as_bytearray()
    context.user_data["vid_photo"] = base64.standard_b64encode(file_bytes).decode()
    await update.message.reply_text(t(lang, 'vid_got_photo'))
    return VID_PROMPT

async def vid_receive_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update.effective_user.id)
    if update.message.text and update.message.text.strip() == "/skip":
        context.user_data["vid_prompt"] = "Smooth cinematic motion, high quality"
    else:
        context.user_data["vid_prompt"] = update.message.text or "Smooth cinematic motion"
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏱ 5s / 480p", callback_data="vd_5_480p"),
            InlineKeyboardButton("⏱ 5s / 720p", callback_data="vd_5_720p"),
        ],
        [
            InlineKeyboardButton("⏱ 10s / 480p", callback_data="vd_10_480p"),
            InlineKeyboardButton("⏱ 10s / 720p", callback_data="vd_10_720p"),
        ],
    ])
    await update.message.reply_text(t(lang, 'vid_settings'), reply_markup=keyboard)
    return VID_SETTINGS

async def vid_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    lang = get_lang(user_id)
    parts = query.data.split("_")
    duration = int(parts[1])
    resolution = parts[2]
    context.user_data["vid_duration"] = duration
    context.user_data["vid_resolution"] = resolution
    endpoint = "bytedance/seedance-2-0-mini-i2v-720p" if resolution == "720p" else "bytedance/seedance-2-0-mini-i2v-480p"

    allowed, reason = can_generate_video(user_id)
    if not allowed:
        await query.edit_message_text(t(lang, 'vid_no_sub', price=STARS_PRICE))
        return ConversationHandler.END

    await query.edit_message_text(t(lang, 'vid_generating', dur=duration, res=resolution))
    photo_b64 = context.user_data.get("vid_photo", "")
    prompt = context.user_data.get("vid_prompt", "Smooth cinematic motion")
    try:
        payload = {
            "image": f"data:image/jpeg;base64,{photo_b64}",
            "prompt": prompt,
            "duration": duration,
            "resolution": resolution,
        }
        url = await wavespeed_request(endpoint, payload)
        await query.message.reply_video(video=url, caption=f"✅ {duration}s / {resolution}\n{prompt[:180]}")
    except Exception as e:
        logger.error(f"Video gen error: {e}")
        await query.message.reply_text(f"Error: {e}")
    return ConversationHandler.END

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update.effective_user.id)
    context.user_data.clear()
    await update.message.reply_text(t(lang, 'cancelled'))
    return ConversationHandler.END

init_db()

async def check_expiring_subscriptions(context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('memory.db')
    c = conn.cursor()
    now = datetime.now(timezone.utc)
    in_3_days = now + timedelta(days=3)
    c.execute("""
        SELECT user_id FROM users
        WHERE status = 'active'
        AND expires_at IS NOT NULL
        AND datetime(expires_at) BETWEEN datetime(?) AND datetime(?)
    """, (now.isoformat(), in_3_days.isoformat()))
    expiring_soon = c.fetchall()
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
        lang = get_lang(user_id)
        try:
            await context.bot.send_message(chat_id=user_id, text=t(lang, 'notify_expiring'))
        except Exception as e:
            logger.error(f"Notify error {user_id}: {e}")

    for (user_id,) in just_expired:
        if user_id in ADMIN_IDS:
            continue
        lang = get_lang(user_id)
        try:
            await context.bot.send_message(chat_id=user_id, text=t(lang, 'notify_expired', price=STARS_PRICE))
            conn2 = sqlite3.connect('memory.db')
            c2 = conn2.cursor()
            c2.execute("UPDATE users SET status='trial' WHERE user_id=?", (user_id,))
            conn2.commit()
            conn2.close()
        except Exception as e:
            logger.error(f"Expired notify error {user_id}: {e}")

async def send_subscribe_invoice(update: Update, lang: str = 'ru'):
    await update.message.reply_invoice(
        title=t(lang, 'sub_title'),
        description=t(lang, 'sub_desc'),
        payload="subscribe_30",
        currency="XTR",
        prices=[LabeledPrice(t(lang, 'sub_label'), STARS_PRICE)],
    )

GIF_FILE_ID = None
GIF_PATH = "/root/tg_bot/bx_logo.mp4"

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global GIF_FILE_ID
    user_id = update.effective_user.id
    username = update.effective_user.username or ""
    if user_id not in ADMIN_IDS:
        row = get_user_status(user_id)
        if not row and get_total_users() >= MAX_USERS:
            lang = 'ru'
            await update.message.reply_text(t(lang, 'start_closed'))
            return
    register_user(user_id, username)
    lang = get_lang(user_id)
    if user_id in ADMIN_IDS:
        caption = t(lang, 'start_admin')
    else:
        caption = t(lang, 'start_user', days=TRIAL_DAYS, price=STARS_PRICE)
    try:
        reply_markup = make_keyboard(lang)
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
        await update.message.reply_text(caption, reply_markup=make_keyboard(lang))

async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_lang(user_id)
    if user_id in ADMIN_IDS:
        await update.message.reply_text(t(lang, 'sub_admin'))
        return
    register_user(user_id, update.effective_user.username or "")
    await send_subscribe_invoice(update, lang)

async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_lang(user_id)
    expires = activate_subscription(user_id)
    exp_str = expires.strftime("%d.%m.%Y")
    await update.message.reply_text(t(lang, 'payment_ok', date=exp_str))

async def check_access(update: Update) -> bool:
    user_id = update.effective_user.id
    lang = get_lang(user_id)
    allowed, reason = is_allowed(user_id)
    if allowed:
        return True
    if reason == "not_registered":
        await update.message.reply_text(t(lang, 'access_not_registered'))
    elif reason == "expired":
        await update.message.reply_text(t(lang, 'access_expired', price=STARS_PRICE))
        await send_subscribe_invoice(update, lang)
    elif reason == "blocked":
        await update.message.reply_text(t(lang, 'access_blocked'))
    return False

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    user_id = update.effective_user.id
    lang = get_lang(user_id)
    user_text = update.message.text

    if context.user_data.get('waiting_topic_name'):
        del context.user_data['waiting_topic_name']
        tid = create_topic(user_id, user_text)
        set_active_topic(context, tid, user_text)
        await update.message.reply_text(t(lang, 'topics_created', name=user_text), parse_mode="Markdown")
        return

    # Handle topic goal input
    if context.user_data.get('topic_awaiting_goal'):
        del context.user_data['topic_awaiting_goal']
        active_id, active_name = get_active_topic(context)
        if active_id:
            # Save goal as first message in topic and respond
            save_topic_message(user_id, active_id, "user", user_text)
            goal_system = get_system(user_id) + f"\n\nАктивная тема: {active_name}. Цель пользователя: {user_text}. Сфокусируйся на этой задаче."
            response = client.messages.create(
                model="claude-sonnet-4-6", max_tokens=1000,
                system=goal_system,
                messages=[{"role": "user", "content": user_text}])
            reply = response.content[0].text
            save_topic_message(user_id, active_id, "assistant", reply)
            await update.message.reply_text(f"[{active_name}] {reply}")
        return

    # Keyboard buttons — check both languages
    btn_image_vals = {STRINGS['ru']['btn_image'], STRINGS['en']['btn_image']}
    btn_video_vals = {STRINGS['ru']['btn_video'], STRINGS['en']['btn_video']}
    btn_topics_vals = {STRINGS['ru']['btn_topics'], STRINGS['en']['btn_topics']}
    btn_sub_vals = {STRINGS['ru']['btn_sub'], STRINGS['en']['btn_sub']}
    btn_help_vals = {STRINGS['ru']['btn_help'], STRINGS['en']['btn_help']}

    if user_text in btn_topics_vals:
        await cmd_topics(update, context)
        return
    if user_text in btn_image_vals:
        await cmd_image(update, context)
        return
    if user_text in btn_video_vals:
        await cmd_video_gen(update, context)
        return
    if user_text in btn_sub_vals:
        await cmd_subscribe(update, context)
        return
    if user_text in btn_help_vals:
        await update.message.reply_text(t(lang, 'help_text'))
        return
    btn_lang_vals = {STRINGS['ru']['btn_lang'], STRINGS['en']['btn_lang']}
    if user_text in btn_lang_vals:
        await cmd_language(update, context)
        return

    try:
        remember_kw = STRINGS[lang].get('remember_keywords', STRINGS['ru']['remember_keywords'])
        if any(word in user_text.lower() for word in remember_kw):
            prefs = get_prefs(user_id)
            new_prefs = prefs + '\n' + user_text if prefs else user_text
            save_prefs(user_id, new_prefs)
            await update.message.reply_text(t(lang, 'remembered'))
            return

        search_context = ""
        if needs_search(user_text, lang):
            await update.message.reply_text(t(lang, 'searching'))
            search_context = web_search(user_text)

        active_id, active_name = get_active_topic(context)
        if active_id:
            save_topic_message(user_id, active_id, "user", user_text)
            history = get_topic_history(user_id, active_id)
            if search_context:
                history[-1]["content"] += f"\n\n[Search results]:\n{search_context}"
            topic_system = get_system(user_id) + f"\n\nActive topic: {active_name}."
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
                history[-1]["content"] += f"\n\n[Search results]:\n{search_context}"
            response = client.messages.create(
                model="claude-sonnet-4-6", max_tokens=1000,
                system=get_system(user_id), messages=history)
            reply = response.content[0].text
            save_message(user_id, "assistant", reply)
            await update.message.reply_text(reply)
            # Auto-detect topic suggestion every 6 messages
            msg_count = get_message_count(user_id)
            if msg_count > 0 and msg_count % 6 == 0:
                try:
                    recent = get_history(user_id)[-6:]
                    detect_prompt = t(lang, 'topics_detect_prompt')
                    det_resp = client.messages.create(
                        model="claude-haiku-4-5-20251001", max_tokens=30,
                        messages=[{"role": "user", "content": detect_prompt + "\n\n" + "\n".join([f"{m['role']}: {m['content'][:100]}" for m in recent])}])
                    detected_name = det_resp.content[0].text.strip()[:40]
                    if detected_name and len(detected_name) > 2:
                        keyboard = InlineKeyboardMarkup([[
                            InlineKeyboardButton(t(lang, 'topics_save_btn'), callback_data=f"topic_autosave_{detected_name}")
                        ]])
                        await update.message.reply_text(
                            f"{t(lang, 'topics_save_as')} *{detected_name}*",
                            parse_mode="Markdown",
                            reply_markup=keyboard
                        )
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Text error: {e}")
        await update.message.reply_text(t(lang, 'error_retry'))

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    user_id = update.effective_user.id
    lang = get_lang(user_id)
    try:
        await update.message.reply_text(t(lang, 'voice_received'))
        file = await context.bot.get_file(update.message.voice.file_id)
        path = f"/tmp/voice_{user_id}.ogg"
        await file.download_to_drive(path)
        segments, _ = whisper.transcribe(path, language="ru")
        text = " ".join([s.text for s in segments])
        os.remove(path)
        if not text.strip():
            await update.message.reply_text(t(lang, 'voice_no_speech'))
            return
        await update.message.reply_text(t(lang, 'voice_recognized', text=text))
        save_message(user_id, "user", text)
        response = client.messages.create(model="claude-sonnet-4-6", max_tokens=1000,
                                          system=get_system(user_id), messages=get_history(user_id))
        reply = response.content[0].text
        save_message(user_id, "assistant", reply)
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"Voice error: {e}")
        await update.message.reply_text(t(lang, 'voice_error'))

async def extract_video_frames(video_path: str, user_id: int, max_frames: int = 4) -> list:
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
            t_sec = i * interval
            frame_path = f"/tmp/frame_{user_id}_{i}.jpg"
            subprocess.run(
                ["ffmpeg", "-y", "-ss", str(t_sec), "-i", video_path,
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
    lang = get_lang(user_id)
    is_circle = update.message.video_note is not None
    try:
        if is_circle:
            await update.message.reply_text(t(lang, 'video_circle_received'))
        else:
            await update.message.reply_text(t(lang, 'video_received'))
        video = update.message.video_note or update.message.video
        if not video:
            await update.message.reply_text(t(lang, 'video_unknown'))
            return
        file = await context.bot.get_file(video.file_id)
        video_path = f"/tmp/media_{user_id}.mp4"
        audio_path = f"/tmp/media_{user_id}.wav"
        await file.download_to_drive(video_path)

        audio_result = subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-vn", "-ar", "16000", "-ac", "1", audio_path],
            capture_output=True, text=True)
        speech_text = ""
        if audio_result.returncode == 0 and os.path.exists(audio_path):
            segments, _ = whisper.transcribe(audio_path, language="ru")
            speech_text = " ".join([s.text for s in segments]).strip()
            os.remove(audio_path)

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
            parts = visual_content.copy()
            prompt = ""
            if speech_text:
                prompt += f"Speech in video: {speech_text}\n\n"
            prompt += caption or t(lang, 'circle_prompt')
            parts.append({"type": "text", "text": prompt})
            messages = get_history(user_id) + [{"role": "user", "content": parts}]
            response = client.messages.create(
                model="claude-sonnet-4-6", max_tokens=1000,
                system=get_system(user_id), messages=messages)
            reply = response.content[0].text
            save_message(user_id, "user", f"[video note] {speech_text or 'no speech'}")
            save_message(user_id, "assistant", reply)
            await update.message.reply_text(reply)
        elif speech_text:
            await update.message.reply_text(t(lang, 'voice_recognized', text=speech_text))
            save_message(user_id, "user", speech_text)
            response = client.messages.create(model="claude-sonnet-4-6", max_tokens=1000,
                                              system=get_system(user_id), messages=get_history(user_id))
            reply = response.content[0].text
            save_message(user_id, "assistant", reply)
            await update.message.reply_text(reply)
        else:
            await update.message.reply_text(t(lang, 'video_no_speech'))
    except Exception as e:
        logger.error(f"Media video error: {e}", exc_info=True)
        await update.message.reply_text(t(lang, 'video_error'))

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_lang(user_id)
    try:
        await update.message.reply_text(t(lang, 'photo_received'))
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        file_bytes = await file.download_as_bytearray()
        image_data = base64.standard_b64encode(file_bytes).decode('utf-8')
        caption = update.message.caption or t(lang, 'photo_caption')
        messages = get_history(user_id) + [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data}},
            {"type": "text", "text": caption}]}]
        response = client.messages.create(model="claude-sonnet-4-6", max_tokens=1500,
                                          system=get_system(user_id), messages=messages)
        reply = response.content[0].text
        save_message(user_id, "user", f"[photo] {caption}")
        save_message(user_id, "assistant", reply)
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"Photo error: {e}")
        await update.message.reply_text(t(lang, 'photo_error'))

GEN_KEYWORDS = [
    'сгенерируй', 'сгенери', 'создай', 'нарисуй', 'generate', 'создайте',
    'сделай картинку', 'сделай изображение', 'сделай фото', 'арт',
    'в стиле', 'render', 'draw', 'image of',
    'создай персонажа', 'сделай персонажа', 'аниме', 'cartoon', 'мультяш'
]

async def handle_smart_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    user_id = update.effective_user.id
    lang = get_lang(user_id)
    caption = (update.message.caption or "").lower()
    if any(kw in caption for kw in GEN_KEYWORDS):
        allowed, reason = can_generate_image(user_id)
        if not allowed:
            await update.message.reply_text(t(lang, 'gen_no_sub', price=STARS_PRICE))
            return
        await update.message.reply_text(t(lang, 'gen_photo_confirm'))
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        file_bytes = await file.download_as_bytearray()
        img_ref = base64.standard_b64encode(file_bytes).decode()
        prompt = update.message.caption or "Create a stylized image based on this photo"
        keyboard = [[
            InlineKeyboardButton("1:1 📷", callback_data="ratio_1:1"),
            InlineKeyboardButton("16:9 🖥", callback_data="ratio_16:9"),
            InlineKeyboardButton("9:16 📱", callback_data="ratio_9:16"),
        ]]
        context.user_data["prompt"] = prompt
        context.user_data["img_ref"] = img_ref
        await update.message.reply_text(t(lang, 'gen_choose_ratio'), reply_markup=InlineKeyboardMarkup(keyboard))
        return
    await handle_photo(update, context)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    user_id = update.effective_user.id
    lang = get_lang(user_id)
    try:
        doc = update.message.document
        caption = update.message.caption or ""
        if doc.mime_type == 'application/pdf':
            await update.message.reply_text(t(lang, 'doc_pdf_received'))
            file = await context.bot.get_file(doc.file_id)
            file_bytes = await file.download_as_bytearray()
            pdf_data = base64.standard_b64encode(file_bytes).decode('utf-8')
            messages = [{"role": "user", "content": [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_data}},
                {"type": "text", "text": caption or t(lang, 'doc_pdf_prompt')}]}]
        elif doc.mime_type in ('image/gif', 'video/mp4') or (doc.file_name and doc.file_name.endswith('.gif')):
            if user_id in ADMIN_IDS:
                await update.message.reply_text(f"file_id: {doc.file_id}")
            else:
                await update.message.reply_text("GIF!")
            return
        elif doc.mime_type and doc.mime_type.startswith('image/'):
            await update.message.reply_text(t(lang, 'doc_img_received'))
            file = await context.bot.get_file(doc.file_id)
            file_bytes = await file.download_as_bytearray()
            image_data = base64.standard_b64encode(file_bytes).decode('utf-8')
            messages = get_history(user_id) + [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": doc.mime_type, "data": image_data}},
                {"type": "text", "text": caption or t(lang, 'doc_img_question')}]}]
        else:
            await update.message.reply_text(t(lang, 'doc_unsupported'))
            return
        response = client.messages.create(model="claude-sonnet-4-6", max_tokens=1500,
                                          system=get_system(user_id), messages=messages)
        reply = response.content[0].text
        save_message(user_id, "user", f"[file] {caption}")
        save_message(user_id, "assistant", reply)
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"Document error: {e}")
        await update.message.reply_text(t(lang, 'doc_error'))

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
        await update.message.reply_text("Reply to a media message with /getid")
        return
    for field in ['animation', 'video', 'document', 'photo', 'voice']:
        obj = getattr(msg, field, None)
        if obj:
            fid = obj[-1].file_id if field == 'photo' else obj.file_id
            await update.message.reply_text(f"{field}_file_id:\n`{fid}`", parse_mode='Markdown')
            return
    await update.message.reply_text("No media found")

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

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
        VID_SETTINGS: [CallbackQueryHandler(vid_settings_callback, pattern="^vd_")],
    },
    fallbacks=[CommandHandler("cancel", cmd_cancel)],
    per_user=True, per_chat=True,
)
app.add_handler(img_conv)
app.add_handler(vid_conv)

app.add_handler(CommandHandler("topics", cmd_topics))
app.add_handler(CallbackQueryHandler(topics_callback, pattern="^topic_"))
app.add_handler(CallbackQueryHandler(lang_callback, pattern="^setlang_"))
app.add_handler(CommandHandler("start", cmd_start))
app.add_handler(CommandHandler("language", cmd_language))
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
print("Bot started v9 — i18n ru/en")
import datetime as dt
app.job_queue.run_daily(
    check_expiring_subscriptions,
    time=dt.time(hour=10, minute=0, tzinfo=timezone.utc)
)
app.run_polling()
