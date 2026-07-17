"""BX Assistant — генерация фото/видео (WaveSpeed) + загрузка файлов."""
import asyncio
import logging
import time

import requests

from ..config import WAVESPEED_KEY

logger = logging.getLogger(__name__)
WS_HEADERS = {"Authorization": f"Bearer {WAVESPEED_KEY}", "Content-Type": "application/json"}


def _ws_poll(request_id, timeout=120):
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
                if status == "failed":
                    logger.error(f"WS failed: {data}")
                    return None
        except Exception as e:
            logger.error(f"ws_poll: {e}")
    return None


def _submit(endpoint, payload, timeout):
    resp = requests.post(f"https://api.wavespeed.ai/api/v3/{endpoint}",
                         headers=WS_HEADERS, json=payload, timeout=15)
    if resp.status_code != 200:
        logger.error(f"WS {endpoint} error: {resp.text}")
        return None
    rid = resp.json().get("data", {}).get("id")
    return _ws_poll(rid, timeout=timeout) if rid else None


def _image_t2i(prompt):
    return _submit("google/nano-banana-2/text-to-image",
                   {"prompt": prompt, "size": "2048*2048", "num_images": 1}, 90)


def _image_edit(image_url, prompt):
    return _submit("google/nano-banana-2/edit",
                   {"image": image_url, "prompt": prompt, "size": "2048*2048"}, 90)


def _video_t2v(prompt):
    return _submit("bytedance/seedance-2.0-mini/text-to-video",
                   {"prompt": prompt, "duration": 5, "resolution": "480p"}, 120)


def _video_i2v(image_url, prompt=""):
    return _submit("bytedance/seedance-2.0-mini/image-to-video",
                   {"image": image_url, "prompt": prompt or "cinematic smooth motion",
                    "duration": 5, "resolution": "480p"}, 120)


# ── Асинхронные обёртки (не блокируют event loop) ─────────────────────────────
async def generate_image(prompt):
    return await asyncio.to_thread(_image_t2i, prompt)


async def generate_image_edit(image_url, prompt):
    return await asyncio.to_thread(_image_edit, image_url, prompt)


async def generate_video_from_text(prompt):
    return await asyncio.to_thread(_video_t2v, prompt)


async def generate_video(image_url, prompt=""):
    return await asyncio.to_thread(_video_i2v, image_url, prompt)


def _upload_bytes(file_bytes):
    upload = requests.post(
        "https://tmpfiles.org/api/v1/upload",
        files={"file": ("photo.jpg", bytes(file_bytes), "image/jpeg")},
        timeout=15)
    if upload.status_code != 200:
        return None
    page_url = upload.json().get("data", {}).get("url", "")
    return page_url.replace("tmpfiles.org/", "tmpfiles.org/dl/")


async def upload_file_id(context, file_id):
    """Скачивает файл из Telegram и выкладывает на временный хостинг для WaveSpeed."""
    file = await context.bot.get_file(file_id)
    file_bytes = await file.download_as_bytearray()
    return await asyncio.to_thread(_upload_bytes, file_bytes)
