# BX Assistant (@agent_bx_bot)

Персональный AI-ассистент на базе Claude Sonnet.

## Возможности
- 💬 Текстовый диалог с памятью
- 🎤 Расшифровка голосовых и видео (Whisper)
- 📷 Анализ фото и PDF (Claude Vision)
- 🌐 Веб-поиск (Tavily)
- 🎨 Генерация изображений (Flux via Wavespeed)
- 🎬 Генерация видео из фото (Seedance 2.0 Mini)
- 💳 Монетизация через Telegram Stars

## Стек
- python-telegram-bot v22
- anthropic claude-sonnet-4-6
- faster-whisper
- wavespeed API
- SQLite

## Деплой
```bash
cp .env.example .env  # заполни токены
pip install -r requirements.txt
systemctl start tgbot
```
