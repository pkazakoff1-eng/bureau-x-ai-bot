import logging
from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)
model = WhisperModel("base", device="cpu", compute_type="int8")

def transcribe(path):
    try:
        segments, _ = model.transcribe(path, language="ru")
        return " ".join([s.text for s in segments])
    except Exception as e:
        logger.error(f"Whisper error: {e}")
        return ""
