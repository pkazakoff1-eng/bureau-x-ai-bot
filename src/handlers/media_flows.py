"""BX Assistant — флоу генерации фото/видео: колбэки и состояния."""
import logging

from telegram import Update
from telegram.ext import ContextTypes

from .. import db
from ..access import gate, spend
from ..services import media
from .commands import IMAGE_KB, VIDEO_KB

logger = logging.getLogger(__name__)

GEN_IMG_WORDS = ["сгенерируй фото", "сгенерируй картинку", "нарисуй",
                 "создай фото", "создай картинку", "сделай фото"]
GEN_VID_WORDS = ["сгенерируй видео", "создай видео", "сделай видео"]
GEN_CAPTION_WORDS = ["сгенерируй", "нарисуй", "создай картинку", "создай фото",
                     "сделай картинку", "на основе", "в стиле мультяш", "мультяшн"]


async def topics_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    data = query.data
    if data == "topic:auto":
        db.set_user_topic(user_id, None)
        await query.edit_message_text("🔄 Тема определяется автоматически.")
    elif data == "topic:new":
        context.user_data["waiting_new_topic"] = True
        await query.edit_message_text(
            "Напиши название темы (и описание через запятую):\n_работа с клиентом, задачи по проекту_",
            parse_mode="Markdown")
    elif data.startswith("topic:"):
        topic = data.split(":", 1)[1]
        db.set_user_topic(user_id, topic)
        notes = db.get_topic_notes(user_id, topic)
        notes_text = f"\n\n📝 Заметки:\n{notes}" if notes else ""
        await query.edit_message_text(
            f"✅ Тема заметок: *{topic.upper()}*{notes_text}\n\nИстория диалога остаётся общей.",
            parse_mode="Markdown")


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
            "🖼 Напиши что изменить, потом пришли фото.\nИли сначала пришли фото — спрошу промпт.")
    elif data == "media:vid_t2v":
        context.user_data["media_mode"] = "vid_t2v"
        await query.edit_message_text("📝 Напиши промпт для видео:")
    elif data == "media:vid_i2v":
        context.user_data["media_mode"] = "vid_i2v"
        await query.edit_message_text("🎬 Пришли фото. Можно сначала написать промпт — или сразу фото.")


async def _do_image_edit(update, context, user_id, file_id_or_url, prompt, from_file_id=True):
    await update.message.reply_text("🎨 Редактирую...")
    if from_file_id:
        direct_url = await media.upload_file_id(context, file_id_or_url)
        if not direct_url:
            await update.message.reply_text("❌ Не удалось загрузить фото. Пришли снова.")
            return False
    else:
        direct_url = file_id_or_url
    url = await media.generate_image_edit(direct_url, prompt)
    if url:
        spend(user_id, "images")
        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=url,
                                     caption=f"✅ Промпт: {prompt[:80]}")
        return True
    return False


async def handle_text_states(update, context) -> bool:
    """Текстовые состояния медиа-флоу. True — сообщение обработано."""
    user_id = update.effective_user.id
    user_text = update.message.text

    # новая тема
    if context.user_data.get("waiting_new_topic"):
        parts = [p.strip() for p in user_text.split(",", 1)]
        name = parts[0].lower()
        description = parts[1] if len(parts) > 1 else ""
        db.add_user_topic(user_id, name, description)
        db.set_user_topic(user_id, name)
        context.user_data.pop("waiting_new_topic", None)
        await update.message.reply_text(
            f"✅ Тема *{name.upper()}* создана!\nВеду историю отдельно.", parse_mode="Markdown")
        return True

    # фото уже прислано — ждём промпт для редактирования
    if context.user_data.get("waiting_img_edit_photo_first"):
        file_id = context.user_data.get("waiting_img_edit_photo_first")
        prompt = user_text if user_text.lower() != "без промпта" else "improve this image"
        if not await gate(update, context, "images"):
            context.user_data.pop("waiting_img_edit_photo_first", None)
            return True
        ok = await _do_image_edit(update, context, user_id, file_id, prompt)
        if ok:
            context.user_data.pop("waiting_img_edit_photo_first", None)
        else:
            await update.message.reply_text("❌ Не удалось. Попробуй изменить промпт:")
        return True

    # фото уже прислано — ждём промпт для видео
    if context.user_data.get("waiting_video_photo_first"):
        file_id = context.user_data.get("waiting_video_photo_first")
        prompt = "" if user_text.lower() == "без промпта" else user_text
        if not await gate(update, context, "videos"):
            context.user_data.pop("waiting_video_photo_first", None)
            return True
        await update.message.reply_text("🎬 Генерирую видео (30-60 сек)...")
        direct_url = await media.upload_file_id(context, file_id)
        if not direct_url:
            context.user_data.pop("waiting_video_photo_first", None)
            await update.message.reply_text("❌ Не удалось загрузить фото. Пришли снова.")
            return True
        url = await media.generate_video(direct_url, prompt)
        if url:
            context.user_data.pop("waiting_video_photo_first", None)
            spend(user_id, "videos")
            await context.bot.send_video(
                chat_id=update.effective_chat.id, video=url,
                caption="✅ Готово!" + (f" Промпт: {prompt}" if prompt else ""))
        else:
            await update.message.reply_text("❌ Не удалось. Попробуй другой промпт:")
        return True

    mode = context.user_data.get("media_mode")
    if mode == "img_t2i":
        if not await gate(update, context, "images"):
            context.user_data.pop("media_mode", None)
            return True
        await update.message.reply_text("🎨 Генерирую...")
        url = await media.generate_image(user_text)
        if url:
            context.user_data.pop("media_mode", None)
            spend(user_id, "images")
            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=url,
                                         caption=f"✅ Промпт: {user_text[:80]}")
        else:
            await update.message.reply_text("❌ Не удалось. Напиши промпт ещё раз:")
        return True

    if mode == "img_i2i":
        context.user_data["img_edit_prompt"] = user_text
        context.user_data["waiting_img_edit"] = True
        context.user_data.pop("media_mode", None)
        await update.message.reply_text(f"✅ Промпт: «{user_text}»\nТеперь пришли фото.")
        return True

    if mode == "vid_t2v":
        if not await gate(update, context, "videos"):
            context.user_data.pop("media_mode", None)
            return True
        await update.message.reply_text("🎬 Генерирую видео (30-60 сек)...")
        url = await media.generate_video_from_text(user_text)
        if url:
            context.user_data.pop("media_mode", None)
            spend(user_id, "videos")
            await context.bot.send_video(chat_id=update.effective_chat.id, video=url,
                                         caption=f"✅ Промпт: {user_text[:80]}")
        else:
            await update.message.reply_text("❌ Не удалось. Напиши промпт ещё раз:")
        return True

    if mode == "vid_i2v":
        context.user_data["video_prompt"] = user_text
        context.user_data["waiting_video"] = True
        context.user_data.pop("media_mode", None)
        await update.message.reply_text(f"✅ Промпт: «{user_text}»\nТеперь пришли фото.")
        return True

    # перехват «сгенерируй …» в обычном чате
    tl = user_text.lower()
    if any(w in tl for w in GEN_IMG_WORDS):
        await update.message.reply_text("🎨 Выбери режим:", reply_markup=IMAGE_KB)
        return True
    if any(w in tl for w in GEN_VID_WORDS):
        await update.message.reply_text("🎬 Выбери режим:", reply_markup=VIDEO_KB)
        return True
    return False


async def handle_photo_states(update, context) -> bool:
    """Фото-состояния медиа-флоу. True — сообщение обработано."""
    user_id = update.effective_user.id

    if context.user_data.get("waiting_img_edit"):
        prompt = context.user_data.get("img_edit_prompt", "improve this image")
        if not await gate(update, context, "images"):
            context.user_data.pop("waiting_img_edit", None)
            context.user_data.pop("img_edit_prompt", None)
            return True
        ok = await _do_image_edit(update, context, user_id,
                                  update.message.photo[-1].file_id, prompt)
        if ok:
            context.user_data.pop("waiting_img_edit", None)
            context.user_data.pop("img_edit_prompt", None)
        else:
            await update.message.reply_text("❌ Не удалось. Пришли фото ещё раз:")
        return True

    if context.user_data.get("waiting_video"):
        prompt = context.user_data.get("video_prompt", "")
        if not await gate(update, context, "videos"):
            context.user_data.pop("waiting_video", None)
            context.user_data.pop("video_prompt", None)
            return True
        await update.message.reply_text("🎬 Генерирую видео (30-60 сек)...")
        direct_url = await media.upload_file_id(context, update.message.photo[-1].file_id)
        if not direct_url:
            await update.message.reply_text("❌ Не удалось загрузить фото. Пришли снова.")
            return True
        url = await media.generate_video(direct_url, prompt)
        if url:
            context.user_data.pop("waiting_video", None)
            context.user_data.pop("video_prompt", None)
            spend(user_id, "videos")
            await context.bot.send_video(
                chat_id=update.effective_chat.id, video=url,
                caption="✅ Готово!" + (f" Промпт: {prompt}" if prompt else ""))
        else:
            await update.message.reply_text("❌ Не удалось. Пришли фото ещё раз:")
        return True

    if context.user_data.get("media_mode") == "img_i2i":
        context.user_data.pop("media_mode", None)
        caption_prompt = update.message.caption
        if caption_prompt:
            if not await gate(update, context, "images"):
                return True
            ok = await _do_image_edit(update, context, user_id,
                                      update.message.photo[-1].file_id, caption_prompt)
            if not ok:
                context.user_data["waiting_img_edit_photo_first"] = update.message.photo[-1].file_id
                await update.message.reply_text("❌ Не удалось. Попробуй другой промпт:")
        else:
            context.user_data["waiting_img_edit_photo_first"] = update.message.photo[-1].file_id
            await update.message.reply_text("Что сделать с фото? Напиши промпт:")
        return True

    if context.user_data.get("media_mode") == "vid_i2v":
        context.user_data.pop("media_mode", None)
        context.user_data["waiting_video_photo_first"] = update.message.photo[-1].file_id
        await update.message.reply_text("Опиши движение для видео (или 'без промпта'):")
        return True

    # авто-триггер i2i по подписи к фото
    photo_caption = update.message.caption or ""
    if any(w in photo_caption.lower() for w in GEN_CAPTION_WORDS):
        if not await gate(update, context, "images"):
            return True
        ok = await _do_image_edit(update, context, user_id,
                                  update.message.photo[-1].file_id, photo_caption)
        if not ok:
            context.user_data["waiting_img_edit_photo_first"] = update.message.photo[-1].file_id
            await update.message.reply_text("❌ Не удалось. Попробуй изменить промпт:")
        return True

    return False
