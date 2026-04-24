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
blocked_users = set()          # 유저ID 차단
blocked_usernames = set()      # username 차단 (@없이 저장)

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


# ---------------- 공통 ---------------- #

def is_blocked(user):
    uid = user.id
    uname = user.username.lower() if user.username else None

    if uid in blocked_users:
        return True

    if uname and uname in blocked_usernames:
        return True

    return False


# ---------------- 시작 ---------------- #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if is_blocked(user):
        await update.message.reply_text("상담이 제한된 계정입니다.")
        return

    keyboard = [
        [InlineKeyboardButton("문의하기", callback_data="contact")]
    ]

    await update.message.reply_text(
        "안녕하세요. 문의를 원하시면 버튼을 눌러주세요.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ---------------- 버튼 ---------------- #

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if is_blocked(query.from_user):
        await query.message.reply_text("상담이 제한된 계정입니다.")
        return

    if query.data == "contact":
        waiting_users.add(query.from_user.id)
        await query.message.reply_text(FORM_TEXT)


# ---------------- 문의 접수 ---------------- #

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text

    if is_blocked(user):
        await update.message.reply_text("상담이 제한된 계정입니다.")
        return

    if user.id in waiting_users:
        username = f"@{user.username}" if user.username else "없음"

        msg = f"""새 문의 접수

이름: {user.full_name}
아이디: {username}

고유번호(복사용):
`{user.id}`

작성 내용:
{text}
"""

        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=msg,
            parse_mode="Markdown"
        )

        await update.message.reply_text("접수 완료되었습니다. 확인 후 연락드리겠습니다.")
        waiting_users.remove(user.id)


# ---------------- 차단 ---------------- #

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return

    try:
        target = context.args[0]

        # 숫자면 ID 차단
        if target.isdigit():
            blocked_users.add(int(target))
            await update.message.reply_text(f"{target} 차단 완료")

        # 문자면 username 차단
        else:
            target = target.replace("@", "").lower()
            blocked_usernames.add(target)
            await update.message.reply_text(f"@{target} 차단 완료")

    except:
        await update.message.reply_text("사용법: /ban 유저ID 또는 /ban 사용자명")


# ---------------- 차단해제 ---------------- #

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return

    try:
        target = context.args[0]

        if target.isdigit():
            blocked_users.discard(int(target))
            await update.message.reply_text(f"{target} 차단 해제 완료")

        else:
            target = target.replace("@", "").lower()
            blocked_usernames.discard(target)
            await update.message.reply_text(f"@{target} 차단 해제 완료")

    except:
        await update.message.reply_text("사용법: /unban 유저ID 또는 /unban 사용자명")


# ---------------- 목록 ---------------- #

async def banlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return

    text = "차단 목록\n\n"

    if blocked_users:
        text += "ID 차단:\n"
        for x in blocked_users:
            text += f"`{x}`\n"

    if blocked_usernames:
        text += "\n아이디 차단:\n"
        for x in blocked_usernames:
            text += f"@{x}\n"

    if not blocked_users and not blocked_usernames:
        text = "차단 목록 없음"

    await update.message.reply_text(text, parse_mode="Markdown")


# ---------------- 실행 ---------------- #

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ban", ban_user))
    app.add_handler(CommandHandler("unban", unban_user))
    app.add_handler(CommandHandler("banlist", banlist))

    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    app.run_polling()


if __name__ == "__main__":
    main()
