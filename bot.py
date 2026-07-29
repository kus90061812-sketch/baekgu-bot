import logging
import random
import uuid
from collections import Counter

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import (
    ADMIN_IDS,
    ATTENDANCE_REWARD,
    BOT_TITLE,
    BOT_TOKEN,
    DATABASE_URL,
    MAX_MULTI_DRAW,
    MAX_RPS_BET,
    MAX_TICKET_PURCHASE,
    MINING_CHANCE,
    MINING_MAX_POINTS,
    MINING_MIN_POINTS,
    MIN_RPS_BET,
    REWARDS,
    TICKET_PRICE,
)
from database import (
    accept_rps_game,
    add_mined_points,
    buy_tickets_with_points,
    change_game_points,
    change_tickets,
    claim_attendance,
    create_rps_game,
    decline_rps_game,
    find_user_by_username,
    get_game_point_ranking,
    get_ranking,
    get_rps_game,
    get_rps_ranking,
    get_rps_stats,
    get_user,
    init_db,
    perform_draws,
    save_rps_choice,
    set_rps_message_id,
    settle_rps_game,
    upsert_user,
)


logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def display_name(user):
    return user.full_name or user.username or str(user.id)


def is_admin(user_id):
    return int(user_id) in ADMIN_IDS


def weighted_reward():
    item = random.choices(
        REWARDS,
        weights=[weight for _, _, weight in REWARDS],
        k=1,
    )[0]
    return item[0], item[1]


async def register_user(update: Update):
    user = update.effective_user
    if user and not user.is_bot:
        upsert_user(user.id, user.username, display_name(user))


def target_from_reply_or_username(update: Update, args):
    message = update.effective_message

    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        return target.id, display_name(target)

    if args and args[0].startswith("@"):
        row = find_user_by_username(args[0])
        if not row:
            raise ValueError("해당 아이디를 찾을 수 없습니다. 먼저 채팅을 한 번 쳐야 합니다.")
        return int(row["user_id"]), row["display_name"]

    raise ValueError("상대 메시지에 답장하거나 @아이디를 입력하세요.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)
    await update.effective_message.reply_text(
        f"🎰 {BOT_TITLE}\n\n"
        "일반 채팅을 칠 때마다 일정 확률로 게임포인트를 얻습니다.\n"
        "/도움말 명령어를 확인하세요."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)
    await update.effective_message.reply_text(
        "📌 명령어\n\n"
        "🎟 뽑기\n"
        "/뽑기 [횟수]\n"
        "/내뽑기권\n"
        "/뽑기랭킹\n"
        "/뽑기권구매 [수량]\n\n"
        "💰 게임포인트\n"
        "/내포인트\n"
        "/포인트랭킹\n"
        "/출석\n\n"
        "✊ 가위바위보\n"
        "상대 메시지에 답장 후 /가위바위보 [금액]\n"
        "/가위바위보전적\n"
        "/가위바위보랭킹\n\n"
        "👑 관리자\n"
        "답장 후 /지급 [수량]\n"
        "답장 후 /회수 [수량]\n"
        "답장 후 /포인트지급 [금액]\n"
        "답장 후 /포인트회수 [금액]"
    )


async def my_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)
    row = get_user(update.effective_user.id)
    await update.effective_message.reply_text(
        f"🎟 보유 뽑기권: {int(row['tickets']):,}장"
    )


async def my_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)
    row = get_user(update.effective_user.id)
    await update.effective_message.reply_text(
        f"💰 게임포인트: {int(row['game_points']):,}P"
    )


async def attendance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)
    ok, balance = claim_attendance(update.effective_user.id, ATTENDANCE_REWARD)

    if not ok:
        await update.effective_message.reply_text("이미 오늘 출석했습니다.")
        return

    await update.effective_message.reply_text(
        f"✅ 출석 완료!\n"
        f"지급: {ATTENDANCE_REWARD:,}P\n"
        f"보유: {balance:,}P"
    )


async def draw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)

    count = 1
    if context.args:
        try:
            count = int(context.args[0])
        except ValueError:
            await update.effective_message.reply_text("사용법: /뽑기 [횟수]")
            return

    if count < 1 or count > MAX_MULTI_DRAW:
        await update.effective_message.reply_text(
            f"한 번에 1~{MAX_MULTI_DRAW}회까지 가능합니다."
        )
        return

    results = [weighted_reward() for _ in range(count)]

    try:
        remaining, total = perform_draws(
            update.effective_user.id,
            results,
            count,
        )
    except ValueError as exc:
        await update.effective_message.reply_text(str(exc))
        return

    counts = Counter(name for name, _ in results)
    lines = [f"🎰 뽑기 결과 ({count}회)"]

    for name, qty in counts.items():
        lines.append(f"• {name} × {qty}")

    lines.extend(
        [
            "",
            f"총 당첨: {total:,}포인트",
            f"남은 뽑기권: {remaining:,}장",
            "최대 당첨: 100,000포인트",
        ]
    )

    await update.effective_message.reply_text("\n".join(lines))


async def ticket_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, sign: int):
    await register_user(update)

    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("관리자 전용 명령어입니다.")
        return

    if not context.args:
        await update.effective_message.reply_text(
            "상대 메시지에 답장 후 수량을 입력하세요."
        )
        return

    try:
        amount = int(context.args[-1]) * sign
        if amount == 0:
            raise ValueError
        target_id, target_name = target_from_reply_or_username(
            update,
            context.args,
        )
        _, after = change_tickets(
            update.effective_user.id,
            target_id,
            amount,
            update.effective_chat.id,
        )
    except ValueError as exc:
        await update.effective_message.reply_text(str(exc))
        return

    action = "지급" if sign > 0 else "회수"
    await update.effective_message.reply_text(
        f"✅ {target_name}님 뽑기권 {abs(amount):,}장 {action}\n"
        f"현재 보유: {after:,}장"
    )


async def give_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ticket_admin(update, context, 1)


async def take_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ticket_admin(update, context, -1)


async def point_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, sign: int):
    await register_user(update)

    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("관리자 전용 명령어입니다.")
        return

    if not context.args:
        await update.effective_message.reply_text(
            "상대 메시지에 답장 후 금액을 입력하세요."
        )
        return

    try:
        amount = int(context.args[-1].replace(",", "")) * sign
        if amount == 0:
            raise ValueError
        target_id, target_name = target_from_reply_or_username(
            update,
            context.args,
        )
        _, after = change_game_points(
            update.effective_user.id,
            target_id,
            amount,
            update.effective_chat.id,
        )
    except ValueError as exc:
        await update.effective_message.reply_text(str(exc))
        return

    action = "지급" if sign > 0 else "회수"
    await update.effective_message.reply_text(
        f"✅ {target_name}님 게임포인트 {abs(amount):,}P {action}\n"
        f"현재 보유: {after:,}P"
    )


async def give_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await point_admin(update, context, 1)


async def take_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await point_admin(update, context, -1)


async def buy_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)

    if not context.args:
        await update.effective_message.reply_text(
            f"사용법: /뽑기권구매 [수량]\n1장 가격: {TICKET_PRICE:,}P"
        )
        return

    try:
        quantity = int(context.args[0])
        if quantity < 1 or quantity > MAX_TICKET_PURCHASE:
            raise ValueError(
                f"한 번에 1~{MAX_TICKET_PURCHASE}장까지 구매할 수 있습니다."
            )

        points, tickets, cost = buy_tickets_with_points(
            update.effective_user.id,
            quantity,
            TICKET_PRICE,
        )
    except ValueError as exc:
        await update.effective_message.reply_text(str(exc))
        return

    await update.effective_message.reply_text(
        f"✅ 뽑기권 {quantity:,}장 구매 완료\n"
        f"사용 포인트: {cost:,}P\n"
        f"남은 포인트: {points:,}P\n"
        f"보유 뽑기권: {tickets:,}장"
    )


async def draw_ranking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = get_ranking(10)

    if not rows:
        await update.effective_message.reply_text("아직 뽑기 기록이 없습니다.")
        return

    lines = ["🏆 뽑기 포인트 랭킹"]
    for idx, row in enumerate(rows, 1):
        lines.append(
            f"{idx}. {row['display_name']} — "
            f"{int(row['total_points']):,}P "
            f"({int(row['total_draws']):,}회)"
        )

    await update.effective_message.reply_text("\n".join(lines))


async def point_ranking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = get_game_point_ranking(10)

    if not rows:
        await update.effective_message.reply_text("아직 포인트 기록이 없습니다.")
        return

    lines = ["🏆 게임포인트 랭킹"]
    for idx, row in enumerate(rows, 1):
        lines.append(
            f"{idx}. {row['display_name']} — {int(row['game_points']):,}P"
        )

    await update.effective_message.reply_text("\n".join(lines))


async def rps_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)

    message = update.effective_message
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply_text("상대 메시지에 답장 후 /가위바위보 [금액]")
        return

    opponent = message.reply_to_message.from_user

    if opponent.is_bot or opponent.id == update.effective_user.id:
        await message.reply_text("본인 또는 봇에게는 도전할 수 없습니다.")
        return

    upsert_user(opponent.id, opponent.username, display_name(opponent))

    if not context.args:
        await message.reply_text("사용법: /가위바위보 [금액]")
        return

    try:
        bet = int(context.args[0].replace(",", ""))
    except ValueError:
        await message.reply_text("금액은 숫자로 입력하세요.")
        return

    if bet < MIN_RPS_BET or bet > MAX_RPS_BET:
        await message.reply_text(
            f"배팅 가능 범위: {MIN_RPS_BET:,}P ~ {MAX_RPS_BET:,}P"
        )
        return

    game_id = uuid.uuid4().hex[:16]

    try:
        create_rps_game(
            game_id,
            update.effective_chat.id,
            update.effective_user.id,
            opponent.id,
            bet,
        )
    except ValueError as exc:
        await message.reply_text(str(exc))
        return

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ 수락",
                    callback_data=f"rps_accept:{game_id}",
                ),
                InlineKeyboardButton(
                    "❌ 거절",
                    callback_data=f"rps_decline:{game_id}",
                ),
            ]
        ]
    )

    sent = await message.reply_text(
        f"✊ 가위바위보 도전!\n"
        f"{display_name(update.effective_user)} → {display_name(opponent)}\n"
        f"배팅: {bet:,}P",
        reply_markup=keyboard,
    )
    set_rps_message_id(game_id, sent.message_id)


def choice_keyboard(game_id):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✌️ 가위",
                    callback_data=f"rps_choice:{game_id}:scissors",
                ),
                InlineKeyboardButton(
                    "✊ 바위",
                    callback_data=f"rps_choice:{game_id}:rock",
                ),
                InlineKeyboardButton(
                    "✋ 보",
                    callback_data=f"rps_choice:{game_id}:paper",
                ),
            ]
        ]
    )


async def rps_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    user_id = query.from_user.id

    try:
        if data.startswith("rps_accept:"):
            game_id = data.split(":", 1)[1]
            accept_rps_game(game_id, user_id)
            await query.edit_message_text(
                "✅ 도전이 수락되었습니다.\n두 참가자는 아래에서 선택하세요.",
                reply_markup=choice_keyboard(game_id),
            )
            return

        if data.startswith("rps_decline:"):
            game_id = data.split(":", 1)[1]
            decline_rps_game(game_id, user_id)
            await query.edit_message_text("❌ 도전이 거절되었습니다.")
            return

        if data.startswith("rps_choice:"):
            _, game_id, choice = data.split(":", 2)
            game = save_rps_choice(game_id, user_id, choice)

            if not game["challenger_choice"] or not game["opponent_choice"]:
                await query.answer("선택 완료! 상대방을 기다리는 중입니다.", show_alert=True)
                return

            c = game["challenger_choice"]
            o = game["opponent_choice"]

            wins = {
                ("scissors", "paper"),
                ("rock", "scissors"),
                ("paper", "rock"),
            }

            if c == o:
                winner_id = None
            elif (c, o) in wins:
                winner_id = int(game["challenger_id"])
            else:
                winner_id = int(game["opponent_id"])

            settle_rps_game(game_id, winner_id)
            game = get_rps_game(game_id)

            labels = {
                "scissors": "가위 ✌️",
                "rock": "바위 ✊",
                "paper": "보 ✋",
            }

            if winner_id is None:
                result = "무승부! 배팅 포인트가 반환되었습니다."
            else:
                winner_name = (
                    game["challenger_display_name"]
                    if winner_id == int(game["challenger_id"])
                    else game["opponent_display_name"]
                )
                result = f"🏆 승자: {winner_name}"

            await query.edit_message_text(
                f"{game['challenger_display_name']}: "
                f"{labels[game['challenger_choice']]}\n"
                f"{game['opponent_display_name']}: "
                f"{labels[game['opponent_choice']]}\n\n"
                f"{result}"
            )
    except ValueError as exc:
        await query.answer(str(exc), show_alert=True)


async def rps_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)
    row = get_rps_stats(update.effective_user.id)

    await update.effective_message.reply_text(
        f"✊ 가위바위보 전적\n"
        f"승: {int(row['wins']):,}\n"
        f"패: {int(row['losses']):,}\n"
        f"무: {int(row['draws']):,}\n"
        f"총 경기: {int(row['games']):,}\n"
        f"순포인트: {int(row['net_points']):,}P"
    )


async def rps_ranking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = get_rps_ranking(10)

    if not rows:
        await update.effective_message.reply_text("아직 가위바위보 기록이 없습니다.")
        return

    lines = ["🏆 가위바위보 랭킹"]
    for idx, row in enumerate(rows, 1):
        lines.append(
            f"{idx}. {row['display_name']} — "
            f"{int(row['wins']):,}승 / "
            f"{int(row['net_points']):,}P"
        )

    await update.effective_message.reply_text("\n".join(lines))


async def chat_mining(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user

    if not message or not user or user.is_bot:
        return

    if not message.text or message.text.startswith("/"):
        return

    await register_user(update)

    if random.random() >= MINING_CHANCE:
        return

    amount = random.randint(MINING_MIN_POINTS, MINING_MAX_POINTS)

    try:
        balance = add_mined_points(user.id, amount)
    except Exception:
        logger.exception("채팅 채굴 지급 실패")
        return

    await message.reply_text(
        f"💰 채팅 보상 당첨!\n"
        f"+{amount:,}P\n"
        f"보유: {balance:,}P"
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled error", exc_info=context.error)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN이 설정되지 않았습니다.")

    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL이 설정되지 않았습니다.")

    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("시작", start))
    app.add_handler(CommandHandler("도움말", help_command))
    app.add_handler(CommandHandler("help", help_command))

    app.add_handler(CommandHandler("내뽑기권", my_tickets))
    app.add_handler(CommandHandler("내포인트", my_points))
    app.add_handler(CommandHandler("출석", attendance))
    app.add_handler(CommandHandler("뽑기", draw))
    app.add_handler(CommandHandler("뽑기랭킹", draw_ranking))
    app.add_handler(CommandHandler("포인트랭킹", point_ranking))
    app.add_handler(CommandHandler("뽑기권구매", buy_tickets))

    app.add_handler(CommandHandler("지급", give_tickets))
    app.add_handler(CommandHandler("회수", take_tickets))
    app.add_handler(CommandHandler("포인트지급", give_points))
    app.add_handler(CommandHandler("포인트회수", take_points))

    app.add_handler(CommandHandler("가위바위보", rps_command))
    app.add_handler(CommandHandler("가위바위보전적", rps_stats))
    app.add_handler(CommandHandler("가위바위보랭킹", rps_ranking))
    app.add_handler(CallbackQueryHandler(rps_callback, pattern=r"^rps_"))

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, chat_mining)
    )

    app.add_error_handler(error_handler)

    logger.info("Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
