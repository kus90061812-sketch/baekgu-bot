import sqlite3
import random
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# =========================
# 설정
# =========================

TOKEN = "여기에_토큰입력"

ADMIN_ID = 7936160142

# =========================
# DB
# =========================

db = sqlite3.connect("bot.db", check_same_thread=False)
cursor = db.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    tickets INTEGER DEFAULT 0
)
""")

db.commit()

# =========================
# 확률
# =========================

rewards = [
    ("3,000포인트", 50),
    ("5,000포인트", 28),
    ("10,000포인트", 15),
    ("30,000포인트", 5),
    ("50,000포인트", 2)
]

def random_reward():
    items = [i[0] for i in rewards]
    weights = [i[1] for i in rewards]

    return random.choices(
        items,
        weights=weights,
        k=1
    )[0]

# =========================
# 사용자 저장
# =========================

def save_user(user):

    cursor.execute(
        """
        INSERT INTO users(user_id, username)
        VALUES(?,?)

        ON CONFLICT(user_id)
        DO UPDATE SET
        username=excluded.username
        """,
        (
            user.id,
            user.username
        )
    )

    db.commit()

# =========================
# 시작
# =========================

async def start(update: Update,
                context: ContextTypes.DEFAULT_TYPE):

    save_user(update.effective_user)

    await update.message.reply_text(
"""🎰 포인트 뽑기봇

환영합니다.

명령어

/도움말"""
    )

# =========================
# 도움말
# =========================

async def help_command(update: Update,
                       context: ContextTypes.DEFAULT_TYPE):

    save_user(update.effective_user)

    await update.message.reply_text(
"""🎰 뽑기 이벤트

🎫 사용자

/뽑기

👑 관리자

/지급 ID 수량

또는

/지급 @아이디 수량

🏆 당첨 목록

🥉 3,000포인트
🥈 5,000포인트
🥇 10,000포인트
💎 30,000포인트
👑 50,000포인트"""
    )
        # =========================
# 지급
# =========================

async def give(update: Update,
               context: ContextTypes.DEFAULT_TYPE):

    save_user(update.effective_user)

    if update.effective_user.id != ADMIN_ID:

        await update.message.reply_text(
            "❌ 관리자만 사용할 수 있습니다."
        )

        return

    if len(context.args) != 2:

        await update.message.reply_text(
"""사용법

/지급 사용자ID 수량

또는

/지급 @아이디 수량"""
        )

        return

    target = context.args[0]

    try:
        amount = int(context.args[1])
    except ValueError:

        await update.message.reply_text(
            "수량은 숫자만 입력하세요."
        )

        return

    # =====================
    # 사용자명 지급
    # =====================

    if target.startswith("@"):

        username = target[1:]

        cursor.execute(
            """
            SELECT user_id
            FROM users
            WHERE username=?
            """,
            (username,)
        )

        row = cursor.fetchone()

        if row is None:

            await update.message.reply_text(
                "❌ 해당 사용자를 찾을 수 없습니다."
            )

            return

        user_id = row[0]

    # =====================
    # ID 지급
    # =====================

    else:

        try:
            user_id = int(target)

        except ValueError:

            await update.message.reply_text(
                "잘못된 사용자 ID입니다."
            )

            return

    cursor.execute(
        """
        INSERT INTO users(user_id,tickets)
        VALUES(?,?)

        ON CONFLICT(user_id)
        DO UPDATE SET
        tickets=tickets+excluded.tickets
        """,
        (
            user_id,
            amount
        )
    )

    db.commit()

    await update.message.reply_text(
        f"✅ 뽑기권 {amount}장 지급 완료"
    )
# =========================
# 뽑기
# =========================

async def draw(update: Update,
               context: ContextTypes.DEFAULT_TYPE):

    save_user(update.effective_user)

    user = update.effective_user

    cursor.execute(
        """
        SELECT tickets
        FROM users
        WHERE user_id=?
        """,
        (user.id,)
    )

    row = cursor.fetchone()

    if row is None or row[0] <= 0:

        await update.message.reply_text(
            "🎫 보유한 뽑기권이 없습니다."
        )

        return

    tickets = row[0]

    # 뽑기권 차감
    cursor.execute(
        """
        UPDATE users
        SET tickets=tickets-1
        WHERE user_id=?
        """,
        (user.id,)
    )

    db.commit()

    reward = random_reward()

    await update.message.reply_text(
f"""🎉 뽑기 결과 🎉

👤 참여자 : {user.first_name}

━━━━━━━━━━━━━━

🏆 당첨 상품

✨ {reward} ✨

━━━━━━━━━━━━━━

🎫 남은 뽑기권 : {tickets-1}장

🍀 축하드립니다!"""
    )


# =========================
# 내 뽑기권
# =========================

async def myticket(update: Update,
                   context: ContextTypes.DEFAULT_TYPE):

    save_user(update.effective_user)

    cursor.execute(
        """
        SELECT tickets
        FROM users
        WHERE user_id=?
        """,
        (update.effective_user.id,)
    )

    row = cursor.fetchone()

    count = row[0] if row else 0

    await update.message.reply_text(
        f"🎫 현재 보유 뽑기권 : {count}장"
    )
                       # =========================
# 실행
# =========================

app = Application.builder().token(TOKEN).build()

# 사용자 명령어
app.add_handler(
    CommandHandler(
        "시작",
        start
    )
)

app.add_handler(
    CommandHandler(
        "도움말",
        help_command
    )
)

app.add_handler(
    CommandHandler(
        "뽑기",
        draw
    )
)

app.add_handler(
    CommandHandler(
        "내뽑기권",
        myticket
    )
)

# 관리자 명령어
app.add_handler(
    CommandHandler(
        "지급",
        give
    )
)

print("Bot is running...")

app.run_polling(
    drop_pending_updates=True
)
                       
