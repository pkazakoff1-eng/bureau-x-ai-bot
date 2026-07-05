import os
import time
import base64
import logging
import requests
from io import BytesIO
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

WAVESPEED_API_KEY = os.getenv("WAVESPEED_API_KEY", "")
API_BASE = "https://api.wavespeed.ai"

MODELS = {
    "seedance": "bytedance/seedance-2.0-mini/text-to-video",
    "seedance_i2v": "bytedance/seedance-2.0-mini/image-to-video",
    "gpt_image_2": "openai/gpt-image-2/text-to-image",
    "gpt_image_2_edit": "openai/gpt-image-2/edit",
}

HEADERS = {"Authorization": f"Bearer {WAVESPEED_API_KEY}", "Content-Type": "application/json"}


def _submit(model, payload):
    url = f"{API_BASE}/api/v3/{model}"
    logger.info(f"Submitting to {url}")
    resp = requests.post(url, headers=HEADERS, json=payload)
    if resp.status_code != 200:
        logger.error(f"API error {resp.status_code}: {resp.text[:500]}")
        raise RuntimeError(f"API error {resp.status_code}: {resp.text[:500]}")
    return resp.json()


def _poll(job_id, result_url=None, max_wait=600):
    url = result_url or f"{API_BASE}/api/v3/predictions/{job_id}/result"
    start = time.time()
    while time.time() - start < max_wait:
        resp = requests.get(url, headers=HEADERS)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("data", {}).get("status") or data.get("status", "unknown")
        if status in ("completed", "succeeded", "done"):
            return data
        if status in ("failed", "error"):
            raise RuntimeError(f"Generation error: {data}")
        time.sleep(5)
    raise TimeoutError("Generation timed out")


def _extract_url(data):
    outputs = data.get("data", {}).get("outputs") or data.get("outputs") or data.get("output")
    if isinstance(outputs, list) and outputs:
        return outputs[0]
    if isinstance(outputs, str):
        return outputs
    return None


def _resize_and_base64(image_bytes, max_size=1024):
    """Resize image to max_size and return base64 data URI."""
    from PIL import Image
    img = Image.open(BytesIO(image_bytes))
    
    # Convert to RGB if necessary
    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')
    
    # Resize maintaining aspect ratio
    img.thumbnail((max_size, max_size), Image.LANCZOS)
    
    # Save to JPEG buffer
    buffer = BytesIO()
    img.save(buffer, format='JPEG', quality=85)
    buffer.seek(0)
    
    b64 = base64.b64encode(buffer.read()).decode()
    return f"data:image/jpeg;base64,{b64}"


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not WAVESPEED_API_KEY:
        await update.message.reply_text("WaveSpeed API key not set. Add WAVESPEED_API_KEY to .env")
        return

    prompt = " ".join(context.args) if context.args else None
    if not prompt:
        await update.message.reply_text(
            "Video generation via WaveSpeed\n"
            "Usage: /video <scene description>\n"
            "Example: /video cinematic drone shot over icelandic waterfall"
        )
        return

    await update.message.reply_text("Generating video, this takes 1-2 minutes...")

    try:
        job = _submit(MODELS["seedance"], {
            "prompt": prompt,
            "duration": 5,
            "resolution": "720p",
            "aspect_ratio": "9:16",
            "generate_audio": True,
        })
        job_id = job.get("data", {}).get("id")
        result_url = job.get("data", {}).get("urls", {}).get("get")
        if not job_id:
            raise RuntimeError(f"No job_id: {job}")

        result = _poll(job_id, result_url)
        video_url = _extract_url(result)

        if video_url:
            await update.message.reply_text(f"Done!\n{video_url}")
        else:
            await update.message.reply_text(f"Generation error: {result}")

    except Exception as e:
        logger.error(f"WaveSpeed error: {e}")
        await update.message.reply_text(f"Error: {e}")


async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not WAVESPEED_API_KEY:
        await update.message.reply_text("WaveSpeed API key not set. Add WAVESPEED_API_KEY to .env")
        return

    prompt = " ".join(context.args) if context.args else None
    if not prompt:
        await update.message.reply_text(
            "Image generation via GPT Image 2\n"
            "Usage: /image <description>\n"
            "Example: /image futuristic city at sunset, neon lights"
        )
        return

    await update.message.reply_text("Generating photo, this takes 20-40 seconds...")

    try:
        job = _submit(MODELS["gpt_image_2"], {
            "prompt": prompt,
            "size": "1024x1024",
        })
        job_id = job.get("data", {}).get("id")
        result_url = job.get("data", {}).get("urls", {}).get("get")
        if not job_id:
            raise RuntimeError(f"No job_id: {job}")

        result = _poll(job_id, result_url)
        image_url = _extract_url(result)

        if image_url:
            await update.message.reply_text(f"Done!\n{image_url}")
        else:
            await update.message.reply_text(f"Generation error: {result}")

    except Exception as e:
        logger.error(f"Image error: {e}")
        await update.message.reply_text(f"Error: {e}")


async def handle_image_to_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Photo + caption -> image-to-image via GPT Image 2 edit."""
    if not WAVESPEED_API_KEY:
        await update.message.reply_text("WaveSpeed API key not set")
        return

    if not update.message.photo:
        await update.message.reply_text("Send a photo with a description")
        return

    prompt = update.message.caption or "Transform this image"
    await update.message.reply_text("Uploading photo and generating image...")

    try:
        # Download photo
        photo = await context.bot.get_file(update.message.photo[-1].file_id)
        photo_bytes = await photo.download_as_bytearray()
        
        # Resize and convert to base64
        data_uri = _resize_and_base64(photo_bytes)

        # Generate image
        job = _submit(MODELS["gpt_image_2_edit"], {
            "images": [data_uri],
            "prompt": prompt,
            "size": "1024x1024",
        })
        job_id = job.get("data", {}).get("id")
        result_url = job.get("data", {}).get("urls", {}).get("get")
        if not job_id:
            raise RuntimeError(f"No job_id: {job}")

        result = _poll(job_id, result_url)
        image_url = _extract_url(result)

        if image_url:
            await update.message.reply_text(f"Done!\n{image_url}")
        else:
            await update.message.reply_text(f"Error: {result}")

    except Exception as e:
        logger.error(f"Image-to-image error: {e}")
        await update.message.reply_text(f"Error: {e}")


async def handle_image_to_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not WAVESPEED_API_KEY:
        await update.message.reply_text("WaveSpeed API key not set")
        return

    if not update.message.photo:
        await update.message.reply_text("Send a photo with a caption to make a video from it")
        return

    prompt = update.message.caption or "Cinematic movement, soft lighting"
    await update.message.reply_text("Uploading photo and generating video...")

    try:
        # Download photo
        photo = await context.bot.get_file(update.message.photo[-1].file_id)
        photo_bytes = await photo.download_as_bytearray()
        
        # Resize and convert to base64
        data_uri = _resize_and_base64(photo_bytes)

        # Generate video
        job = _submit(MODELS["seedance_i2v"], {
            "image": data_uri,
            "prompt": prompt,
            "duration": 5,
            "resolution": "720p",
            "aspect_ratio": "9:16",
            "generate_audio": True,
        })
        job_id = job.get("data", {}).get("id")
        result_url = job.get("data", {}).get("urls", {}).get("get")
        if not job_id:
            raise RuntimeError(f"No job_id: {job}")

        result = _poll(job_id, result_url)
        video_url = _extract_url(result)

        if video_url:
            await update.message.reply_text(f"Done!\n{video_url}")
        else:
            await update.message.reply_text(f"Error: {result}")

    except Exception as e:
        logger.error(f"Image-to-video error: {e}")
        await update.message.reply_text(f"Error: {e}")
