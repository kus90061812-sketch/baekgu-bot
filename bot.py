import os
import random
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters
)


TOKEN = os.environ.get("8999195481:AAHgynutwqksHttyHEjUe86nwexayAwAqQk")

ADMIN_ID = 7936160142


tickets = {}


rewards = [
    ("1,000포인트", 50),
    ("3,000포인트", 30),
    ("5,000포인트", 15),
    ("10,000포인트", 4),
    ("50,000포인트", 1),
]


def random_reward():
    items = []

    for reward, weight in rewards:
        items.extend([reward] * weight)

    return random.choice(items)



async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        """
🎰 뽑기봇 정상 작동중!

사용 가능한 명령어

/도움말
/뽑기
"""
    )



async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        """
🎰 뽑기봇 명령어

🎫 사용자

/뽑기
➡️ 랜덤 뽑기


👑 관리자

/지급 사용자ID 수량
➡️ 뽑기권 지급


/시작
➡️ 봇 작동 확인
"""
    )



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


    tickets[user_id] = tickets.get(user_id, 0) + amount


    await update.message.reply_text(
        f"🎫 {amount}장 지급 완료"
    )



async def draw(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id
    name = update.effective_user.first_name


    if tickets.get(user_id, 0) <= 0:

        await update.message.reply_text(
            "🎫 보유한 뽑기권이 없습니다."
        )
        return


    tickets[user_id] -= 1

    reward = random_reward()


    await update.message.reply_text(
        f"""
🎉 뽑기 결과 🎉

👤 {name}

🏆 당첨 : {reward}

🎫 남은 뽑기권 : {tickets[user_id]}장
"""
    )



async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = update.message.text


    if text == "/시작":
        await start(update, context)

    elif text == "/도움말":
        await help_command(update, context)

    elif text == "/뽑기":
        await draw(update, context)

    elif text.startswith("/지급"):
        await give(update, context)



if not TOKEN:
    print("TOKEN 없음")
    exit()


app = ApplicationBuilder().token(TOKEN).build()


app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        message_handler
    )
)

app.add_handler(
    MessageHandler(
        filters.Regex(r"^/(시작|도움말|뽑기|지급).*"),
        message_handler
    )
)


print("Bot is running")

app.run_polling()
