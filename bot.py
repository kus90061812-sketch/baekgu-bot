from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = "8507319540:AAEvjdcDatnUl-lpA1hC_nMCJvjcwOrzUAA"
ADMIN_CHAT_ID = 8402128598

waiting_users = set()

FORM_TEXT = """아래 양식 복사 후 작성해주세요.

✔️ 성함 :
✔️ 생년월일 :
✔️ 거주지 :
✔️ 연락처 :
✔️ 직업 :
✔️ 필요금액 :
✔️ 개인돈 사용유무(갯수) :
✔️ 개인돈 사고유무(이력) :
"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("문의하기", callback_data="contact")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "안녕하세요. 문의를 원하시면 버튼을 눌러주세요.",
        reply_markup=reply_markup
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "contact":
        waiting_users.add(query.from_user.id)
        await query.message.reply_text(FORM_TEXT)


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text

    if user.id in waiting_users:
        username = f"@{user.username}" if user.username else "없음"

        msg = f"""새 문의 접수

이름: {user.full_name}
아이디: {username}
유저ID: {user.id}

작성 내용:
{text}
"""

        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg)
        await update.message.reply_text("접수 완료되었습니다. 확인 후 연락드리겠습니다.")
        waiting_users.remove(user.id)


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    app.run_polling()


if __name__ == "__main__":
    main()
