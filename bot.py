import random
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = "8999195481:AAH9bv-jqgrQ0KgDPwHsbhPLGTHSpsAxBE4"

ADMIN_ID = 7936160142  # 관리자 텔레그램 ID

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


async def give(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) != 2:
        await update.message.reply_text("/give 사용자ID 수량")
        return

    uid = int(context.args[0])
    amount = int(context.args[1])

    tickets[uid] = tickets.get(uid, 0) + amount

    await update.message.reply_text(f"{amount}장 지급 완료")


async def draw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if tickets.get(uid, 0) <= 0:
        await update.message.reply_text("🎫 보유한 뽑기권이 없습니다.")
        return

    tickets[uid] -= 1

    reward = random_reward()

    await update.message.reply_text(
        f"""🎉 뽑기 결과

🏆 {reward} 당첨!

남은 뽑기권 : {tickets[uid]}장"""
    )


app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("give", give))
app.add_handler(CommandHandler("draw", draw))

print("봇 실행")
app.run_polling()
