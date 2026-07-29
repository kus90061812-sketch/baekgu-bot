import os

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

ADMIN_IDS = {
    int(x.strip())
    for x in os.environ.get("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
BOT_TITLE = os.environ.get("BOT_TITLE", "행운 뽑기봇").strip()

MAX_MULTI_DRAW = 50

# 형식: ("표시 이름", 실제 지급 포인트, 가중치)
# 가중치 합계 100
REWARDS = [
    ("3,000포인트", 3000, 50.0),
    ("5,000포인트", 5000, 30.0),
    ("10,000포인트", 10000, 14.5),
    ("30,000포인트", 30000, 3.0),
    ("50,000포인트", 50000, 2.0),
    ("100,000포인트", 100000, 0.5),
]

# 일반 채팅을 칠 때마다 확률 판정
MINING_CHANCE = 0.08
MINING_MIN_POINTS = 5
MINING_MAX_POINTS = 20

# 출석
ATTENDANCE_REWARD = 50

# 게임포인트 → 뽑기권
TICKET_PRICE = 1000
MAX_TICKET_PURCHASE = 100

# 가위바위보
MIN_RPS_BET = 100
MAX_RPS_BET = 1_000_000
