FROM python:3.12-slim

# System deps: ffmpeg for audio/video, build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot
COPY bot.py .
COPY bx_logo.mp4 .

# Persistent volume for DB
VOLUME ["/app/data"]

CMD ["python3", "-u", "bot.py"]
