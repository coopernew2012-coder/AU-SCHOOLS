import os
from dotenv import load_dotenv

load_dotenv()
BRAND_NAME = "AU Schools"
BOT_USERNAME = os.getenv("BOT_USERNAME", BRAND_NAME)
ACCENT_COLOR = 0x155E32
BRAND_GOLD = 0xFFD52A
try:
    MAIN_GUILD_ID = int(os.getenv("MAIN_GUILD_ID", "1563853559944974366"))
except ValueError:
    MAIN_GUILD_ID = 0
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_INVITE = os.getenv("DISCORD_INVITE", "")
DATA_DIR = os.getenv("DATA_DIR", "data")
DATABASE_PATH = os.path.join(DATA_DIR, "bot.db")
SOFTBAN_CHANNEL_ID = ""
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
_port_env = os.getenv("PORT", "")
WEB_PORT = int(_port_env) if _port_env.isdigit() and int(_port_env) > 0 else 8080
