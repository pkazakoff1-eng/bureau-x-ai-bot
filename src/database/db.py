import sqlite3

DB_PATH = 'memory.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS messages (user_id INTEGER, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS preferences (user_id INTEGER PRIMARY KEY, prefs TEXT DEFAULT '')''')
    c.execute('''CREATE TABLE IF NOT EXISTS summaries (user_id INTEGER PRIMARY KEY, summary TEXT DEFAULT '', updated DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

def get_history(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT role, content FROM messages WHERE user_id = ? ORDER BY timestamp DESC LIMIT 20', (user_id,))
    rows = c.fetchall()
    conn.close()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

def get_message_count(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM messages WHERE user_id = ?', (user_id,))
    count = c.fetchone()[0]
    conn.close()
    return count

def save_message(user_id, role, content):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)', (user_id, role, content))
    conn.commit()
    conn.close()

def get_summary(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT summary FROM summaries WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else ''

def save_summary(user_id, summary):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO summaries (user_id, summary, updated) VALUES (?, ?, CURRENT_TIMESTAMP)', (user_id, summary))
    conn.commit()
    conn.close()

def get_prefs(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT prefs FROM preferences WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else ''

def save_prefs(user_id, prefs):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO preferences (user_id, prefs) VALUES (?, ?)', (user_id, prefs))
    conn.commit()
    conn.close()
