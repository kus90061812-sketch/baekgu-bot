import asyncio
import random
import sqlite3

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters,
)


# =====================
# 기본 설정
# =====================

# Railway Variables에 TOKEN을 등록하면 자동으로 불러옵니다.
# 환경변수를 쓰지 않을 경우 아래 기본값을 실제 토큰으로 바꿔도 됩니다.
TOKEN = "8999195481:AAHgynutwqksHttyHEjUe86nwexayAwAqQk"

# 관리자 텔레그램 숫자 ID
ADMIN_ID = 7936160142


# =====================
# SQLite 설정
# =====================

db = sqlite3.connect(
    "bot.db",
    check_same_thread=False,
)

cursor = db.cursor()

cursor.execute(
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        tickets INTEGER DEFAULT 0
    )
    """
)

# 기존 bot.db에 username 컬럼이 없을 때 자동 추가
cursor.execute("PRAGMA table_info(users)")
columns = {row[1] for row in cursor.fetchall()}

if "username" not in columns:
    cursor.execute("ALTER TABLE users ADD COLUMN username TEXT")

db.commit()


# 동시에 여러 번 뽑는 문제 방지
draw_lock = asyncio.Lock()


# =====================
# 당첨 확률
# 합계 100
# =====================

rewards = [
    ("3,000포인트", 50),
    ("5,000포인트", 28),
    ("10,000포인트", 15),
    ("30,000포인트", 5),
    ("50,000포인트", 2),
]


def random_reward() -> str:
    items = [reward for reward, _ in rewards]
    weights = [weight for _, weight in rewards]

    return random.choices(
        items,
        weights=weights,
        k=1,
    )[0]


# =====================
# 사용자 정보 저장
# =====================

def save_user(user) -> None:
    if user is None:
        return

    cursor.execute(
        """
        INSERT INTO users (user_id, username, tickets)
        VALUES (?, ?, 0)
        ON CONFLICT(user_id)
        DO UPDATE SET username = excluded.username
        """,
        (user.id, user.username),
    )

    db.commit()


# =====================
# 시작
# =====================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    save_user(update.effective_user)

    await update.message.reply_text(
        """🎰 포인트 뽑기봇

정상 작동 중입니다.

/도움말 입력"""
    )


# =====================
# 도움말
# =====================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    save_user(update.effective_user)

    await update.message.reply_text(
        """🎰 뽑기 이벤트 안내

🎫 사용자 명령어

/뽑기
➡️ 랜덤 뽑기

/내뽑기권
➡️ 보유 뽑기권 확인

👑 관리자 명령어

/지급 사용자ID 수량
/지급 @사용자명 수량
➡️ 뽑기권 지급

🏆 당첨 목록

🥉 3,000포인트
🥈 5,000포인트
🥇 10,000포인트
💎 30,000포인트
👑 50,000포인트"""
    )


# =====================
# 관리자 지급
# =====================

async def give(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(
            "❌ 관리자만 사용할 수 있습니다."
        )
        return

    args = update.message.text.split()

    if len(args) != 3:
        await update.message.reply_text(
            """사용법

/지급 사용자ID 수량

또는

/지급 @사용자명 수량"""
        )
        return

    target = args[1].strip()

    try:
        amount = int(args[2])
    except ValueError:
        await update.message.reply_text(
            "❌ 수량은 숫자로 입력하세요."
        )
        return

    if amount <= 0:
        await update.message.reply_text(
            "❌ 지급 수량은 1장 이상이어야 합니다."
        )
        return

    # 운영 정책상 인당 하루 최대 5장이라면
    # 관리자가 한 번에 5장을 넘겨 지급하지 못하게 제한
    if amount > 5:
        await update.message.reply_text(
            "❌ 한 번에 최대 5장까지만 지급할 수 있습니다."
        )
        return

    display_target = target

    # @사용자명으로 지급
    if target.startswith("@"):
        username = target[1:].strip()

        if not username:
            await update.message.reply_text(
                "❌ 사용자명을 정확히 입력하세요."
            )
            return

        cursor.execute(
            """
            SELECT user_id
            FROM users
            WHERE LOWER(username) = LOWER(?)
            """,
            (username,),
        )

        row = cursor.fetchone()

        if row is None:
            await update.message.reply_text(
                """❌ 해당 사용자명을 찾을 수 없습니다.

사용자가 먼저 봇방이나 그룹에서
/시작 또는 /도움말을 한 번 입력해야 합니다."""
            )
            return

        user_id = row[0]

    # 숫자 ID로 지급
    else:
        try:
            user_id = int(target)
        except ValueError:
            await update.message.reply_text(
                "❌ 사용자 ID 또는 @사용자명을 정확히 입력하세요."
            )
            return

    cursor.execute(
        """
        INSERT INTO users (user_id, tickets)
        VALUES (?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET tickets = tickets + excluded.tickets
        """,
        (user_id, amount),
    )

    db.commit()

    await update.message.reply_text(
        f"""✅ 지급 완료

👤 대상 : {display_target}
🎫 지급 수량 : {amount}장"""
    )


# =====================
# 내 뽑기권
# =====================

async def my_ticket(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    save_user(update.effective_user)

    user_id = update.effective_user.id

    cursor.execute(
        "SELECT tickets FROM users WHERE user_id = ?",
        (user_id,),
    )

    row = cursor.fetchone()
    count = row[0] if row else 0

    await update.message.reply_text(
        f"🎫 현재 보유 뽑기권 : {count}장"
    )


# =====================
# 뽑기
# =====================

async def draw(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    async with draw_lock:
        save_user(update.effective_user)

        user_id = update.effective_user.id
        name = update.effective_user.first_name or "참여자"

        cursor.execute(
            "SELECT tickets FROM users WHERE user_id = ?",
            (user_id,),
        )

        row = cursor.fetchone()
        count = row[0] if row else 0

        if count <= 0:
            await update.message.reply_text(
                "🎫 보유한 뽑기권이 없습니다."
            )
            return

        # 보유권이 있을 때만 1장 차감
        cursor.execute(
            """
            UPDATE users
            SET tickets = tickets - 1
            WHERE user_id = ?
              AND tickets > 0
            """,
            (user_id,),
        )

        if cursor.rowcount != 1:
            db.rollback()
            await update.message.reply_text(
                "❌ 뽑기권 처리 중 오류가 발생했습니다."
            )
            return

        db.commit()

        reward = random_reward()

        await update.message.reply_text(
            f"""🎉 뽑기 결과 🎉

👤 참여자 : {name}

━━━━━━━━━━━━━━

🏆 당첨 상품

✨ {reward} ✨

━━━━━━━━━━━━━━

🎫 남은 뽑기권 : {count - 1}장

🍀 축하드립니다!"""
        )


# =====================
# 한글 명령어 처리
# =====================

async def message_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.message or not update.message.text:
        return

    save_user(update.effective_user)

    text = update.message.text.strip()

    # 그룹에서는 /시작@봇아이디 형태로 들어올 수도 있으므로
    # 명령어 뒤의 @봇아이디를 제거해 처리
    command = text.split()[0].split("@")[0]

    if command == "/시작":
        await start(update, context)

    elif command == "/도움말":
        await help_command(update, context)

    elif command == "/뽑기":
        await draw(update, context)

    elif command == "/내뽑기권":
        await my_ticket(update, context)

    elif command == "/지급":
        await give(update, context)


# =====================
# 오류 처리
# =====================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    print(f"오류 발생: {context.error}")
