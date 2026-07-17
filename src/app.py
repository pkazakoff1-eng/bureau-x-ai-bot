"""BX Assistant — сборка приложения."""
import logging

from telegram.ext import (ApplicationBuilder, CallbackQueryHandler, CommandHandler,
                          MessageHandler, filters)

from . import db
from .config import TELEGRAM_TOKEN
from .handlers import chat, commands, media_flows

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def on_error(update, context):
    logger.error("Unhandled error", exc_info=context.error)


def build_app():
    db.init_db()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.job_queue.run_repeating(chat.check_reminders, interval=60, first=10)

    app.add_handler(CommandHandler("start",  commands.cmd_start))
    app.add_handler(CommandHandler("help",   commands.cmd_help))
    app.add_handler(CommandHandler("reset",  commands.cmd_reset))
    app.add_handler(CommandHandler("image",  commands.cmd_image))
    app.add_handler(CommandHandler("video",  commands.cmd_video))
    app.add_handler(CommandHandler("status", commands.cmd_status))
    app.add_handler(CommandHandler("admin",  commands.cmd_admin))
    app.add_handler(CommandHandler("topics", commands.cmd_topics))
    app.add_handler(CallbackQueryHandler(media_flows.topics_callback, pattern="^topic:"))
    app.add_handler(CallbackQueryHandler(media_flows.media_callback,  pattern="^media:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat.handle_text))
    app.add_handler(MessageHandler(filters.VOICE, chat.handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, chat.handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, chat.handle_document))
    app.add_error_handler(on_error)
    return app
