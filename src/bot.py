import logging
from telegram.ext import ApplicationBuilder, MessageHandler, filters
from src.database.db import init_db
from src.handlers.text import handle_text
from src.handlers.media import handle_voice, handle_photo, handle_document
from src.config import TELEGRAM_TOKEN

logging.basicConfig(level=logging.INFO)

def main():
    init_db()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    print("Бот запущен v5 - правильная структура...")
    app.run_polling()

if __name__ == "__main__":
    main()
