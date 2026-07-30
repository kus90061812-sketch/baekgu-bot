import asyncio
import logging
import random
import uuid
from collections import Counter
from decimal import Decimal, InvalidOperation
from fractions import Fraction

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
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
    MAX_ODD_EVEN_BET,
    MINING_CHANCE,
    MINING_MAX_POINTS,
    MINING_MIN_POINTS,
    MIN_ODD_EVEN_BET,
    MIN_RPS_BET,
    ODD_EVEN_PAYOUT,
    REWARDS,
    TICKET_PRICE,
    RPS_RECRUIT_TIMEOUT,
    RPS_CHOICE_TIMEOUT,
)
from database import (
    add_mined_points,
    buy_tickets_with_points,
    cancel_rps_game,
    change_game_points,
    change_tickets,
    claim_attendance,
    claim_point_drop,
    cleanup_expired_rps_games,
    create_odd_even_game,
    create_point_drop,
    create_rps_game,
    find_user_by_username,
    get_game_point_ranking,
    get_odd_even_ranking,
    get_odd_even_stats,
    get_ranking,
    get_rps_game,
    get_rps_ranking,
    get_rps_stats,
    get_user,
    init_db,
    join_rps_game,
    perform_draws,
    refund_odd_even_game,
    save_rps_choice,
    set_point_drop_message_id,
    set_rps_message_id,
    settle_odd_even_game,
    settle_rps_game,
    upsert_user,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def get_odd_even_payout_ratio():
    try:
        payout = Decimal(str(ODD_EVEN_PAYOUT))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise RuntimeError(
            "config.py의 ODD_EVEN_PAYOUT 값이 올바르지 않습니다."
        ) from exc

    if payout <= 0:
        raise RuntimeError(
            "config.py의 ODD_EVEN_PAYOUT은 0보다 커야 합니다."
        )

    ratio = Fraction(payout)
    return ratio.numerator, ratio.denominator


def format_multiplier(value):
    try:
        decimal_value = Decimal(str(value))
        return format(decimal_value.normalize(), "f")
    except (InvalidOperation, ValueError, TypeError):
        return str(value)


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
            raise ValueError(
                "해당 아이디를 찾을 수 없습니다. 먼저 채팅을 한 번 입력해야 합니다."
            )
        return int(row["user_id"]), row["display_name"]

    raise ValueError("상대 메시지에 답장하거나 @아이디를 입력하세요.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)
    await update.effective_message.reply_text(
        f"🎰 {BOT_TITLE}\n\n"
        "일반 채팅을 입력할 때 일정 확률로 게임포인트를 얻습니다.\n"
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
        "/출석\n"
        "/뿌리기 [총포인트] [인원] (관리자)\n\n"
        "✊ 가위바위보\n"
        "/가위바위보 [금액]\n"
        "/가위바위보전적\n"
        "/가위바위보랭킹\n\n"
        "🎲 홀짝\n"
        "/홀짝 [홀/짝] [금액]\n"
        "/홀짝전적\n"
        "/홀짝랭킹\n"
        f"적중 시 원금 포함 {format_multiplier(ODD_EVEN_PAYOUT)}배 반환\n\n"
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
    ok, balance = claim_attendance(
        update.effective_user.id,
        ATTENDANCE_REWARD,
    )

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
            raise ValueError("수량은 0이 될 수 없습니다.")
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
            raise ValueError("금액은 0이 될 수 없습니다.")
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


async def point_drop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)

    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("관리자 전용 명령어입니다.")
        return

    if len(context.args) != 2:
        await update.effective_message.reply_text(
            "사용법: /뿌리기 [총포인트] [인원]"
        )
        return

    try:
        total_points = int(context.args[0].replace(",", ""))
        max_claims = int(context.args[1].replace(",", ""))
        drop_id = uuid.uuid4().hex[:16]
        points_per_claim = create_point_drop(
            drop_id,
            update.effective_chat.id,
            update.effective_user.id,
            total_points,
            max_claims,
        )
    except ValueError as exc:
        await update.effective_message.reply_text(str(exc))
        return

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(
            f"🎁 받기 (0/{max_claims})",
            callback_data=f"drop_claim:{drop_id}",
        )]]
    )

    sent = await update.effective_message.reply_text(
        f"🎁 포인트 뿌리기!\n\n"
        f"총 포인트: {total_points:,}P\n"
        f"선착순: {max_claims:,}명\n"
        f"1명당: {points_per_claim:,}P",
        reply_markup=keyboard,
    )
    set_point_drop_message_id(drop_id, sent.message_id)


async def point_drop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    upsert_user(user.id, user.username, display_name(user))

    drop_id = (query.data or "").split(":", 1)[1]

    try:
        result = claim_point_drop(drop_id, user.id)
    except ValueError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    await query.answer(
        f"{result['points']:,}P 획득! 현재 {result['balance']:,}P",
        show_alert=True,
    )

    if result["status"] == "finished":
        await query.edit_message_text(
            f"✅ 포인트 뿌리기 종료!\n\n"
            f"총 포인트: {result['total_points']:,}P\n"
            f"지급 인원: {result['max_claims']:,}명\n"
            f"1명당: {result['points_per_claim']:,}P"
        )
    else:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(
                f"🎁 받기 ({result['claimed_count']}/{result['max_claims']})",
                callback_data=f"drop_claim:{drop_id}",
            )]]
        )
        await query.edit_message_reply_markup(reply_markup=keyboard)


def rps_join_keyboard(game_id):
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(
            "⚔️ 참가하기",
            callback_data=f"rps_join:{game_id}",
        )]]
    )


def rps_choice_keyboard(game_id):
    return InlineKeyboardMarkup(
        [[
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
        ]]
    )


async def safe_edit(application, chat_id, message_id, text, reply_markup=None):
    if not message_id:
        return
    try:
        await application.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
        )
    except BadRequest as exc:
        if "Message is not modified" not in str(exc):
            logger.warning("메시지 수정 실패: %s", exc)
    except Exception:
        logger.exception("메시지 수정 실패")


async def process_expired_rps_games(application):
    try:
        expired_games = await asyncio.to_thread(
            cleanup_expired_rps_games
        )
    except Exception:
        logger.exception("만료된 가위바위보 정리 실패")
        return

    for result in expired_games:
        if result["previous_status"] == "pending":
            text = "⌛ 참가자가 없어 가위바위보가 자동 취소되었습니다."
        else:
            text = (
                "⌛ 선택 시간이 초과되어 게임이 자동 취소되었습니다.\n"
                "양쪽 배팅 포인트가 모두 반환되었습니다."
            )

        await safe_edit(
            application,
            result["chat_id"],
            result["message_id"],
            text,
        )


async def rps_expiry_worker(application):
    while True:
        try:
            await process_expired_rps_games(application)
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("가위바위보 만료 감시 오류")
            await asyncio.sleep(5)


async def post_init(application):
    await process_expired_rps_games(application)
    application.bot_data["rps_expiry_task"] = asyncio.create_task(
        rps_expiry_worker(application)
    )


async def rps_recruit_timeout(application, game_id):
    await asyncio.sleep(RPS_RECRUIT_TIMEOUT)
    result = cancel_rps_game(game_id, "pending")

    if not result:
        return

    await safe_edit(
        application,
        result["chat_id"],
        result["message_id"],
        "⌛ 참가자가 없어 가위바위보가 자동 취소되었습니다.",
    )


async def rps_choice_timeout(application, game_id):
    await asyncio.sleep(RPS_CHOICE_TIMEOUT)
    result = cancel_rps_game(game_id, "playing")

    if not result:
        return

    await safe_edit(
        application,
        result["chat_id"],
        result["message_id"],
        "⌛ 선택 시간이 초과되어 게임이 자동 취소되었습니다.\n"
        "양쪽 배팅 포인트가 모두 반환되었습니다.",
    )


async def rps_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)

    if not context.args:
        await update.effective_message.reply_text(
            "사용법: /가위바위보 [금액]"
        )
        return

    try:
        bet = int(context.args[0].replace(",", ""))
    except ValueError:
        await update.effective_message.reply_text("금액은 숫자로 입력하세요.")
        return

    if bet < MIN_RPS_BET or bet > MAX_RPS_BET:
        await update.effective_message.reply_text(
            f"배팅 가능 범위: {MIN_RPS_BET:,}P ~ {MAX_RPS_BET:,}P"
        )
        return

    game_id = uuid.uuid4().hex[:16]

    try:
        create_rps_game(
            game_id,
            update.effective_chat.id,
            update.effective_user.id,
            bet,
        )
    except ValueError as exc:
        await update.effective_message.reply_text(str(exc))
        return

    sent = await update.effective_message.reply_text(
        f"✊ 공개 가위바위보 참가자 모집!\n\n"
        f"개설자: {display_name(update.effective_user)}\n"
        f"배팅: {bet:,}P\n"
        f"제한시간: {RPS_RECRUIT_TIMEOUT}초\n\n"
        "먼저 참가 버튼을 누른 회원과 대결합니다.",
        reply_markup=rps_join_keyboard(game_id),
    )
    set_rps_message_id(game_id, sent.message_id)

    context.application.create_task(
        rps_recruit_timeout(context.application, game_id)
    )


async def rps_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""
    user = query.from_user

    upsert_user(user.id, user.username, display_name(user))

    try:
        if data.startswith("rps_join:"):
            game_id = data.split(":", 1)[1]
            game = join_rps_game(game_id, user.id)

            await query.answer("참가 완료!")
            await query.edit_message_text(
                f"✅ 상대가 정해졌습니다!\n\n"
                f"{game['challenger_display_name']} VS "
                f"{game['opponent_display_name']}\n"
                f"배팅: {int(game['bet']):,}P\n"
                f"선택 제한시간: {RPS_CHOICE_TIMEOUT}초\n\n"
                "두 참가자는 아래에서 선택하세요.",
                reply_markup=rps_choice_keyboard(game_id),
            )

            context.application.create_task(
                rps_choice_timeout(context.application, game_id)
            )
            return

        if data.startswith("rps_choice:"):
            _, game_id, choice = data.split(":", 2)
            game = save_rps_choice(game_id, user.id, choice)

            if not game["challenger_choice"] or not game["opponent_choice"]:
                await query.answer(
                    "선택 완료! 상대방을 기다리는 중입니다.",
                    show_alert=True,
                )
                return

            challenger_choice = game["challenger_choice"]
            opponent_choice = game["opponent_choice"]

            wins = {
                ("scissors", "paper"),
                ("rock", "scissors"),
                ("paper", "rock"),
            }

            if challenger_choice == opponent_choice:
                winner_id = None
            elif (challenger_choice, opponent_choice) in wins:
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
                result_text = "무승부! 양쪽 배팅 포인트가 반환되었습니다."
            else:
                winner_name = (
                    game["challenger_display_name"]
                    if winner_id == int(game["challenger_id"])
                    else game["opponent_display_name"]
                )
                result_text = (
                    f"🏆 승자: {winner_name}\n"
                    f"획득: {int(game['bet']) * 2:,}P"
                )

            await query.answer("게임 종료!")
            await query.edit_message_text(
                f"✊ 가위바위보 결과\n\n"
                f"{game['challenger_display_name']}: "
                f"{labels[game['challenger_choice']]}\n"
                f"{game['opponent_display_name']}: "
                f"{labels[game['opponent_choice']]}\n\n"
                f"{result_text}"
            )
            return

        await query.answer("잘못된 버튼입니다.", show_alert=True)

    except ValueError as exc:
        await query.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("가위바위보 콜백 오류")
        try:
            await query.answer("처리 중 오류가 발생했습니다.", show_alert=True)
        except Exception:
            pass


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
        await update.effective_message.reply_text(
            "아직 가위바위보 기록이 없습니다."
        )
        return

    lines = ["🏆 가위바위보 랭킹"]
    for idx, row in enumerate(rows, 1):
        lines.append(
            f"{idx}. {row['display_name']} — "
            f"{int(row['wins']):,}승 / "
            f"{int(row['net_points']):,}P"
        )

    await update.effective_message.reply_text("\n".join(lines))


async def odd_even_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)

    if len(context.args) != 2:
        await update.effective_message.reply_text(
            "사용법: /홀짝 [홀/짝] [금액]\n"
            "예시: /홀짝 홀 1000\n"
            f"적중 시 원금 포함 {format_multiplier(ODD_EVEN_PAYOUT)}배 반환"
        )
        return

    choice_map = {
        "홀": "odd",
        "홀수": "odd",
        "odd": "odd",
        "짝": "even",
        "짝수": "even",
        "even": "even",
    }
    choice = choice_map.get(context.args[0].lower())

    if not choice:
        await update.effective_message.reply_text("홀 또는 짝을 입력하세요.")
        return

    try:
        bet = int(context.args[1].replace(",", ""))
    except ValueError:
        await update.effective_message.reply_text("금액은 숫자로 입력하세요.")
        return

    if bet < MIN_ODD_EVEN_BET or bet > MAX_ODD_EVEN_BET:
        await update.effective_message.reply_text(
            f"배팅 가능 범위: "
            f"{MIN_ODD_EVEN_BET:,}P ~ {MAX_ODD_EVEN_BET:,}P"
        )
        return

    try:
        payout_numerator, payout_denominator = get_odd_even_payout_ratio()
    except RuntimeError as exc:
        logger.error(str(exc))
        await update.effective_message.reply_text(
            "홀짝 배당 설정에 오류가 있습니다. 관리자에게 문의하세요."
        )
        return

    game_id = uuid.uuid4().hex[:16]

    try:
        create_odd_even_game(
            game_id,
            update.effective_chat.id,
            update.effective_user.id,
            choice,
            bet,
        )
    except ValueError as exc:
        await update.effective_message.reply_text(str(exc))
        return

    try:
        dice_message = await context.bot.send_dice(
            chat_id=update.effective_chat.id,
            emoji="🎲",
            reply_to_message_id=update.effective_message.message_id,
        )
        dice_value = int(dice_message.dice.value)

        await asyncio.sleep(4)

        result = settle_odd_even_game(
            game_id,
            dice_value,
            payout_numerator,
            payout_denominator,
        )
    except Exception:
        logger.exception("홀짝 처리 실패")
        refund_odd_even_game(game_id)
        await update.effective_message.reply_text(
            "주사위 처리 중 오류가 발생해 배팅 포인트를 반환했습니다."
        )
        return

    selected_label = "홀" if choice == "odd" else "짝"
    actual_label = "홀" if dice_value % 2 else "짝"

    if result["won"]:
        result_text = (
            "✅ 적중!\n"
            f"총 반환: {result['payout']:,}P\n"
            f"순이익: {result['net']:,}P"
        )
    else:
        result_text = f"❌ 실패!\n손실: {bet:,}P"

    await update.effective_message.reply_text(
        f"🎲 홀짝 결과\n\n"
        f"선택: {selected_label}\n"
        f"배팅: {bet:,}P\n"
        f"주사위: {dice_value} ({actual_label})\n\n"
        f"{result_text}\n"
        f"현재 보유: {result['balance']:,}P"
    )


async def odd_even_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)
    row = get_odd_even_stats(update.effective_user.id)

    await update.effective_message.reply_text(
        f"🎲 홀짝 전적\n"
        f"승: {int(row['wins']):,}\n"
        f"패: {int(row['losses']):,}\n"
        f"총 경기: {int(row['games']):,}\n"
        f"순포인트: {int(row['net_points']):,}P"
    )


async def odd_even_ranking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = get_odd_even_ranking(10)

    if not rows:
        await update.effective_message.reply_text("아직 홀짝 기록이 없습니다.")
        return

    lines = ["🏆 홀짝 랭킹"]
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


async def korean_command_router(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message
    if not message or not message.text:
        return

    raw = message.text.strip()
    if not raw.startswith("/"):
        return

    first, *rest = raw.split()
    command = first[1:].split("@", 1)[0]
    context.args = rest

    handlers = {
        "시작": start,
        "도움말": help_command,
        "내뽑기권": my_tickets,
        "내포인트": my_points,
        "출석": attendance,
        "뿌리기": point_drop_command,
        "뽑기": draw,
        "뽑기랭킹": draw_ranking,
        "포인트랭킹": point_ranking,
        "뽑기권구매": buy_tickets,
        "지급": give_tickets,
        "회수": take_tickets,
        "포인트지급": give_points,
        "포인트회수": take_points,
        "가위바위보": rps_command,
        "가위바위보전적": rps_stats,
        "가위바위보랭킹": rps_ranking,
        "홀짝": odd_even_command,
        "홀짝전적": odd_even_stats,
        "홀짝랭킹": odd_even_ranking,
    }

    handler = handlers.get(command)
    if handler:
        await handler(update, context)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled error", exc_info=context.error)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN이 설정되지 않았습니다.")

    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL이 설정되지 않았습니다.")

    init_db()

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(rps_callback, pattern=r"^rps_"))
    app.add_handler(
        CallbackQueryHandler(point_drop_callback, pattern=r"^drop_claim:")
    )
    app.add_handler(
        MessageHandler(filters.Regex(r"^/[^\s]+"), korean_command_router),
        group=0,
    )
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, chat_mining),
        group=1,
    )
    app.add_error_handler(error_handler)

    logger.info("Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
