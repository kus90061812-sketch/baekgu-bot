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
    ("3,000포인트", 3000, 70),
    ("5,000포인트", 5000, 18),
    ("10,000포인트", 10000, 8),
    ("30,000포인트", 30000, 2.5),
    ("50,000포인트", 50000, 1.2),
    ("100,000포인트", 100000, 0.3),
]

# 일반 채팅을 칠 때마다 확률 판정
MINING_CHANCE = 0.20
MINING_MIN_POINTS = 1
MINING_MAX_POINTS = 20

# 출석
ATTENDANCE_REWARD = 50

# 게임포인트 → 뽑기권
TICKET_PRICE = 1000
MAX_TICKET_PURCHASE = 100

# 가위바위보
MIN_RPS_BET = 10
MAX_RPS_BET = 100000
RPS_RECRUIT_TIMEOUT = 20   # 참가 모집 시간
RPS_CHOICE_TIMEOUT = 20    # 선택 시간

# 홀짝
MIN_ODD_EVEN_BET = 50
MAX_ODD_EVEN_BET = 100000
ODD_EVEN_PAYOUT = 1.9


