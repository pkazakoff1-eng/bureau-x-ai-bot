"""BX Assistant — база данных (SQLite): схема, миграции, CRUD."""
import sqlite3
from datetime import date, datetime, timedelta

from .config import (DB_PATH, DEFAULT_TOPICS, OWNER_ID, VIP_ID,
                     TIER_OWNER, TIER_VIP, TIER_TRIAL, TIER_LIMITS,
                     GLOBAL_LIMITS, TRIAL_DAYS)


def _conn():
    return sqlite3.connect(DB_PATH)


# ══════════════════════════════════════════════════════════════════════════════
# INIT + MIGRATIONS
# ══════════════════════════════════════════════════════════════════════════════
def _table_cols(c, table):
    return [r[1] for r in c.execute(f"PRAGMA table_info({table})")]


def init_db():
    conn = _conn()
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
        user_id INTEGER, title TEXT DEFAULT '', remind_at DATETIME,
        fired INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        tier TEXT DEFAULT 'trial',
        name TEXT DEFAULT '',
        username TEXT DEFAULT '',
        trial_start DATETIME DEFAULT CURRENT_TIMESTAMP,
        created DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS daily_usage (
        user_id INTEGER, day TEXT,
        messages INTEGER DEFAULT 0,
        images INTEGER DEFAULT 0,
        videos INTEGER DEFAULT 0,
        PRIMARY KEY(user_id, day))""")
    c.execute("""CREATE TABLE IF NOT EXISTS global_usage (
        day TEXT PRIMARY KEY,
        messages INTEGER DEFAULT 0,
        images INTEGER DEFAULT 0,
        videos INTEGER DEFAULT 0)""")

    # ── Мягкие миграции (добавление колонок) ─────────────────────────────────
    for sql in [
        "ALTER TABLE users ADD COLUMN tier TEXT DEFAULT 'trial'",
        "ALTER TABLE users ADD COLUMN name TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN username TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN trial_start DATETIME DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE topics ADD COLUMN description TEXT DEFAULT ''",
        "ALTER TABLE messages ADD COLUMN topic TEXT DEFAULT 'общее'",
        "ALTER TABLE reminders ADD COLUMN title TEXT DEFAULT ''",
        "ALTER TABLE reminders ADD COLUMN fired INTEGER DEFAULT 0",
    ]:
        try:
            c.execute(sql)
        except sqlite3.OperationalError:
            pass

    # ── Жёсткие миграции старых схем ─────────────────────────────────────────
    # messages без id PRIMARY KEY → пересборка (иначе история сортируется нестабильно)
    if "id" not in _table_cols(c, "messages"):
        c.execute("ALTER TABLE messages RENAME TO messages_old")
        c.execute("""CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, role TEXT, content TEXT,
            topic TEXT DEFAULT 'общее',
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)""")
        c.execute("""INSERT INTO messages (user_id, role, content, topic, timestamp)
                     SELECT user_id, role, content, topic, timestamp
                     FROM messages_old ORDER BY timestamp""")
        c.execute("DROP TABLE messages_old")

    # topics со старым PK topic_id → пересборка на PRIMARY KEY(user_id, name)
    if "topic_id" in _table_cols(c, "topics"):
        c.execute("ALTER TABLE topics RENAME TO topics_old")
        c.execute("""CREATE TABLE topics
            (user_id INTEGER, name TEXT, description TEXT DEFAULT '',
             PRIMARY KEY(user_id, name))""")
        c.execute("""INSERT OR IGNORE INTO topics (user_id, name, description)
                     SELECT user_id, name, COALESCE(description,'') FROM topics_old""")
        c.execute("DROP TABLE topics_old")

    # старый тир beta → trial
    c.execute("UPDATE users SET tier='trial' WHERE tier='beta'")
    # у кого нет trial_start — ставим сейчас
    c.execute("UPDATE users SET trial_start=CURRENT_TIMESTAMP WHERE trial_start IS NULL")

    c.execute("INSERT OR IGNORE INTO users (user_id, tier, name) VALUES (?,?,?)",
              (OWNER_ID, TIER_OWNER, "Pavel"))
    c.execute("INSERT OR IGNORE INTO users (user_id, tier, name) VALUES (?,?,?)",
              (VIP_ID, TIER_VIP, "VIP"))
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# MESSAGES
# ══════════════════════════════════════════════════════════════════════════════
def get_history(user_id, topic=None, limit=30):
    conn = _conn()
    c = conn.cursor()
    if topic and topic != "общее":
        c.execute("""SELECT role, content FROM messages
                     WHERE user_id=? AND topic=? ORDER BY id DESC LIMIT ?""",
                  (user_id, topic, limit))
    else:
        c.execute("""SELECT role, content FROM messages
                     WHERE user_id=? ORDER BY id DESC LIMIT ?""", (user_id, limit))
    rows = c.fetchall()
    conn.close()
    messages = []
    for role, content in reversed(rows):
        if content and content.startswith("[фото]"):
            caption = content[6:].strip()
            content = f"пользователь прислал фото{': ' + caption if caption else ''}"
        messages.append({"role": role, "content": content})
    # история для API должна начинаться с user
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages


def save_message(user_id, role, content, topic="общее"):
    conn = _conn()
    conn.execute("INSERT INTO messages (user_id, role, content, topic) VALUES (?,?,?,?)",
                 (user_id, role, content, topic))
    conn.commit()
    conn.close()


def clear_history(user_id, topic=None):
    conn = _conn()
    if topic:
        conn.execute("DELETE FROM messages WHERE user_id=? AND topic=?", (user_id, topic))
    else:
        conn.execute("DELETE FROM messages WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()


def get_message_count(user_id):
    conn = _conn()
    n = conn.execute("SELECT COUNT(*) FROM messages WHERE user_id=?", (user_id,)).fetchone()[0]
    conn.close()
    return n


def last_topic_info(user_id):
    """Тема и время последнего сообщения пользователя (для липкой темы)."""
    conn = _conn()
    row = conn.execute(
        "SELECT topic, timestamp FROM messages WHERE user_id=? ORDER BY id DESC LIMIT 1",
        (user_id,)).fetchone()
    conn.close()
    return row


def topic_message_count(user_id, topic):
    conn = _conn()
    n = conn.execute("SELECT COUNT(*) FROM messages WHERE user_id=? AND topic=?",
                     (user_id, topic)).fetchone()[0]
    conn.close()
    return n


# ══════════════════════════════════════════════════════════════════════════════
# PREFS / SUMMARY
# ══════════════════════════════════════════════════════════════════════════════
def get_prefs(user_id):
    conn = _conn()
    row = conn.execute("SELECT prefs FROM preferences WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row[0] if row else ""


def save_prefs(user_id, prefs):
    conn = _conn()
    conn.execute("INSERT OR REPLACE INTO preferences (user_id, prefs) VALUES (?,?)",
                 (user_id, prefs))
    conn.commit()
    conn.close()


def get_summary(user_id):
    conn = _conn()
    row = conn.execute("SELECT summary FROM summaries WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row[0] if row else ""


def save_summary(user_id, summary):
    conn = _conn()
    conn.execute("INSERT OR REPLACE INTO summaries (user_id, summary, updated) VALUES (?,?,CURRENT_TIMESTAMP)",
                 (user_id, summary))
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# USERS / TIERS / TRIAL
# ══════════════════════════════════════════════════════════════════════════════
def get_user(user_id):
    conn = _conn()
    row = conn.execute("SELECT tier, trial_start FROM users WHERE user_id=?",
                       (user_id,)).fetchone()
    conn.close()
    return row  # (tier, trial_start) | None


def get_tier(user_id):
    row = get_user(user_id)
    return row[0] if row else TIER_TRIAL


def set_tier(user_id, tier, name="", username=""):
    conn = _conn()
    c = conn.cursor()
    exists = c.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone()
    if exists:
        c.execute("UPDATE users SET tier=? WHERE user_id=?", (tier, user_id))
        if name:
            c.execute("UPDATE users SET name=?, username=? WHERE user_id=?",
                      (name, username, user_id))
    else:
        c.execute("INSERT INTO users (user_id, tier, name, username) VALUES (?,?,?,?)",
                  (user_id, tier, name, username))
    conn.commit()
    conn.close()


def ensure_user(user_id, name="", username=""):
    """Регистрирует нового пользователя (trial). True если новый."""
    if get_user(user_id) is None:
        set_tier(user_id, TIER_TRIAL, name, username)
        return True
    return False


def trial_days_left(user_id):
    """Сколько дней trial осталось. None — тариф не trial."""
    row = get_user(user_id)
    if not row or row[0] != TIER_TRIAL:
        return None
    try:
        start = datetime.fromisoformat(str(row[1]).split(".")[0])
    except (ValueError, TypeError):
        return TRIAL_DAYS
    left = (start + timedelta(days=TRIAL_DAYS) - datetime.utcnow()).days + 1
    return max(0, left)


def trial_expired(user_id):
    left = trial_days_left(user_id)
    return left is not None and left <= 0


def list_users():
    conn = _conn()
    rows = conn.execute(
        "SELECT user_id, tier, name, username, trial_start FROM users ORDER BY tier, user_id"
    ).fetchall()
    conn.close()
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# USAGE / LIMITS
# ══════════════════════════════════════════════════════════════════════════════
_FIELDS = {"messages": 0, "images": 1, "videos": 2}


def get_usage(user_id):
    today = date.today().isoformat()
    conn = _conn()
    row = conn.execute("SELECT messages, images, videos FROM daily_usage WHERE user_id=? AND day=?",
                       (user_id, today)).fetchone()
    conn.close()
    return row if row else (0, 0, 0)


def get_global_usage():
    today = date.today().isoformat()
    conn = _conn()
    row = conn.execute("SELECT messages, images, videos FROM global_usage WHERE day=?",
                       (today,)).fetchone()
    conn.close()
    return row if row else (0, 0, 0)


def inc_usage(user_id, field, count_global=True):
    assert field in _FIELDS
    today = date.today().isoformat()
    conn = _conn()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO daily_usage (user_id, day) VALUES (?,?)", (user_id, today))
    c.execute(f"UPDATE daily_usage SET {field}={field}+1 WHERE user_id=? AND day=?",
              (user_id, today))
    if count_global:
        c.execute("INSERT OR IGNORE INTO global_usage (day) VALUES (?)", (today,))
        c.execute(f"UPDATE global_usage SET {field}={field}+1 WHERE day=?", (today,))
    conn.commit()
    conn.close()


def check_limit(user_id, field):
    tier = get_tier(user_id)
    return get_usage(user_id)[_FIELDS[field]] < TIER_LIMITS[tier][_FIELDS[field]]


def check_global_limit(field):
    return get_global_usage()[_FIELDS[field]] < GLOBAL_LIMITS[field]


def limit_of(user_id, field):
    return TIER_LIMITS[get_tier(user_id)][_FIELDS[field]]


# ══════════════════════════════════════════════════════════════════════════════
# TOPICS
# ══════════════════════════════════════════════════════════════════════════════
def get_user_topic(user_id):
    conn = _conn()
    row = conn.execute("SELECT current_topic FROM user_topic WHERE user_id=?",
                       (user_id,)).fetchone()
    conn.close()
    return row[0] if row else None


def set_user_topic(user_id, topic):
    conn = _conn()
    if topic is None:
        conn.execute("DELETE FROM user_topic WHERE user_id=?", (user_id,))
    else:
        conn.execute("INSERT OR REPLACE INTO user_topic (user_id, current_topic) VALUES (?,?)",
                     (user_id, topic))
    conn.commit()
    conn.close()


def get_user_topics(user_id):
    conn = _conn()
    custom = {r[0]: r[1] for r in conn.execute(
        "SELECT name, description FROM topics WHERE user_id=?", (user_id,))}
    conn.close()
    result = dict(DEFAULT_TOPICS)
    result.update(custom)
    return result


def add_user_topic(user_id, name, description=""):
    conn = _conn()
    conn.execute("INSERT OR REPLACE INTO topics (user_id, name, description) VALUES (?,?,?)",
                 (user_id, name.lower(), description))
    conn.commit()
    conn.close()


def get_all_notes(user_id):
    """Заметки по всем темам пользователя — для сквозной памяти."""
    conn = _conn()
    rows = conn.execute(
        "SELECT topic, notes FROM topic_notes WHERE user_id=? AND notes != ''",
        (user_id,)).fetchall()
    conn.close()
    return rows


def get_topic_notes(user_id, topic):
    conn = _conn()
    row = conn.execute("SELECT notes FROM topic_notes WHERE user_id=? AND topic=?",
                       (user_id, topic)).fetchone()
    conn.close()
    return row[0] if row else ""


def save_topic_notes(user_id, topic, notes):
    conn = _conn()
    conn.execute("""INSERT OR REPLACE INTO topic_notes (user_id, topic, notes, updated)
                    VALUES (?,?,?,CURRENT_TIMESTAMP)""", (user_id, topic, notes))
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# REMINDERS
# ══════════════════════════════════════════════════════════════════════════════
def save_reminder(user_id, title, remind_at):
    conn = _conn()
    conn.execute("INSERT INTO reminders (user_id, title, remind_at) VALUES (?,?,?)",
                 (user_id, title, remind_at))
    conn.commit()
    conn.close()


def get_due_reminders():
    conn = _conn()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = conn.execute("SELECT id, user_id, title FROM reminders WHERE fired=0 AND remind_at<=?",
                        (now,)).fetchall()
    conn.close()
    return rows


def mark_reminder_fired(rid):
    conn = _conn()
    conn.execute("UPDATE reminders SET fired=1 WHERE id=?", (rid,))
    conn.commit()
    conn.close()
