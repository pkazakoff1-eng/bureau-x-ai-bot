import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
ANTHROPIC_KEY = os.getenv('ANTHROPIC_KEY')
TAVILY_KEY = os.getenv('TAVILY_KEY')
