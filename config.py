import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "7936160142").split(",")
    if x.strip().isdigit()
}
DB_PATH = os.getenv("DB_PATH", "bot.db").strip() or "bot.db"
BOT_TITLE = os.getenv("BOT_TITLE", "9WIN 럭키뽑기").strip()
MAX_MULTI_DRAW = 50
REWARDS = [
    ("1,000포인트", 1000, 50),
    ("3,000포인트", 3000, 30),
    ("5,000포인트", 5000, 15),
    ("10,000포인트", 10000, 4),
    ("50,000포인트", 50000, 1),
]
