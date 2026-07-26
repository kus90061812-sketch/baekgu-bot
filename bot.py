import os
import random
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = "8999195481:AAHgynutwqksHttyHEjUe86nwexayAwAqQk"

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



async def 시작(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎰 뽑기봇 정상 작동중!\n\n"
        "/도움말 을 입력하면 명령어를 확인할 수 있습니다."
    )



async def 도움말(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        """
🎰 뽑기봇 명령어 안내


👑 관리자 명령어

/지급 사용자ID 수량
➡️ 뽑기권 지급


🎫 사용자 명령어

/뽑기
➡️ 랜덤 뽑기 실행


ℹ️ 안내

/시작
➡️ 봇 작동 확인

/도움말
➡️ 명령어 확인
"""
    )



async def 지급(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:
        return


    if len(context.args) != 2:
        await update.message.reply_text(
            "사용법 : /지급 사용자ID 수량"
        )
        return


    user_id = int(context.args[0])
    amount = int(context.args[1])


    tickets[user_id] = tickets.get(user_id, 0) + amount


    await update.message.reply_text(
        f"🎫 {amount}장 지급 완료"
    )



async def 뽑기(update: Update, context: ContextTypes.DEFAULT_TYPE):

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



app = ApplicationBuilder().token(TOKEN).build()


app.add_handler(CommandHandler("시작", 시작))
app.add_handler(CommandHandler("도움말", 도움말))
app.add_handler(CommandHandler("지급", 지급))
app.add_handler(CommandHandler("뽑기", 뽑기))


print("Bot is running")

app.run_polling()
