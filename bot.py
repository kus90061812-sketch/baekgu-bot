import random
import sqlite3
import asyncio

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters
)


# =====================
# 봇 토큰
# =====================

TOKEN = "8999195481:AAHgynutwqksHttyHEjUe86nwexayAwAqQk"


# 관리자 ID
ADMIN_ID = 7936160142


# =====================
# SQLite 설정
# =====================

db = sqlite3.connect(
    "bot.db",
    check_same_thread=False
)

cursor = db.cursor()


cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    tickets INTEGER DEFAULT 0
)
""")

db.commit()


# 연속 클릭 방지
draw_lock = asyncio.Lock()



# =====================
# 당첨 목록
# =====================

rewards = [
    ("1,000포인트", 50),
    ("3,000포인트", 30),
    ("5,000포인트", 15),
    ("10,000포인트", 4),
    ("50,000포인트", 1)
]



def random_reward():

    result = []

    for reward, weight in rewards:
        result.extend([reward] * weight)

    return random.choice(result)



# =====================
# 시작
# =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        """
🎰 포인트 뽑기봇

정상 작동중입니다.

/도움말 입력
"""
    )



# =====================
# 도움말
# =====================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        """
🎰 뽑기 이벤트 안내


🎫 사용자 명령어

/뽑기
➡️ 랜덤 뽑기


👑 관리자 명령어

/지급 사용자ID 수량
➡️ 뽑기권 지급


🏆 당첨 목록

🥉 1,000포인트
🥈 3,000포인트
🥇 5,000포인트
💎 10,000포인트
👑 최대 당첨금 : 50,000포인트
"""
    )



# =====================
# 관리자 지급
# =====================

async def give(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:
        return


    args = update.message.text.split()


    if len(args) != 3:

        await update.message.reply_text(
            "사용법 : /지급 사용자ID 수량"
        )

        return


    user_id = int(args[1])
    amount = int(args[2])


    cursor.execute(
        """
        INSERT INTO users(user_id, tickets)
        VALUES (?, ?)

        ON CONFLICT(user_id)
        DO UPDATE SET tickets = tickets + ?
        """,
        (user_id, amount, amount)
    )

    db.commit()


    await update.message.reply_text(
        f"🎫 {amount}장 지급 완료"
    )



# =====================
# 뽑기
# =====================

async def draw(update: Update, context: ContextTypes.DEFAULT_TYPE):

    async with draw_lock:

        user_id = update.effective_user.id
        name = update.effective_user.first_name


        cursor.execute(
            "SELECT tickets FROM users WHERE user_id=?",
            (user_id,)
        )

        data = cursor.fetchone()


        count = data[0] if data else 0


        if count <= 0:

            await update.message.reply_text(
                "🎫 보유한 뽑기권이 없습니다."
            )

            return


        cursor.execute(
            """
            UPDATE users
            SET tickets = tickets - 1
            WHERE user_id=?
            """,
            (user_id,)
        )

        db.commit()


        reward = random_reward()


        await update.message.reply_text(
            f"""
🎉 뽑기 결과 🎉

👤 참여자 : {name}

━━━━━━━━━━━━━━

🏆 당첨 상품

✨ {reward} ✨

━━━━━━━━━━━━━━

🎫 남은 뽑기권 : {count - 1}장

💰 최대 당첨금

👑 50,000포인트

🍀 다음 행운의 주인공은?
"""
        )



# =====================
# 한글 명령어 처리
# =====================

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message:
        return


    text = update.message.text


    if text == "/시작":

        await start(update, context)


    elif text == "/도움말":

        await help_command(update, context)


    elif text == "/뽑기":

        await draw(update, context)


    elif text.startswith("/지급"):

        await give(update, context)



# =====================
# 실행
# =====================

app = ApplicationBuilder().token(TOKEN).build()


app.add_handler(
    MessageHandler(
        filters.TEXT,
        message_handler
    )
)


print("Bot is running")


app.run_polling()
