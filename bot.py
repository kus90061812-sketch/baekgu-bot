import logging
import random
import re
import secrets
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import (
    ADMIN_IDS,
    BOT_TITLE,
    BOT_TOKEN,
    MAX_MULTI_DRAW,
    MAX_RPS_BET,
    MAX_TICKET_PURCHASE,
    MINING_CHANCE,
    MINING_COOLDOWN_SECONDS,
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
    change_tickets,
    change_game_points,
    claim_mining_attempt,
    create_rps_game,
    decline_rps_game,
    find_user_by_username,
    get_draw_history,
    get_game_point_ranking,
    get_ranking,
    get_rps_game,
    get_rps_ranking,
    get_rps_stats,
    get_user,
    has_active_rps,
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

COMMAND_RE = re.compile(
    r"^/(?P<command>"
    r"시작|도움말|등록|내뽑기권|뽑기|지급|회수|랭킹|당첨내역|"
    r"내포인트|포인트랭킹|뽑기권구매|"
    r"가위바위보|가위바위보전적|가위바위보랭킹|포인트지급|포인트회수"
    r")(?:@[A-Za-z0-9_]+)?(?:\s+(?P<args>.*))?$"
)

RPS_LABELS = {
    "scissors": "✌️ 가위",
    "rock": "✊ 바위",
    "paper": "✋ 보",
}


def is_group(update):
    return bool(
        update.effective_chat
        and update.effective_chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
    )


def register(user):
    if user and not user.is_bot:
        upsert_user(user.id, user.username, user.full_name or "이름 없음")


def uname(user):
    return f"@{escape(user.username)}" if user.username else escape(user.full_name)


def row_name(row):
    if row["username"]:
        return f"@{escape(row['username'])}"
    return escape(row["display_name"])


def main_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🎰 1회 뽑기", callback_data="draw:1"),
                InlineKeyboardButton("🎟 내 뽑기권", callback_data="tickets"),
            ],
            [
                InlineKeyboardButton("💎 내 게임포인트", callback_data="gamepoints"),
                InlineKeyboardButton("🏆 랭킹", callback_data="ranking"),
            ],
        ]
    )


def rps_accept_keyboard(game_id):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ 수락", callback_data=f"rps:accept:{game_id}"),
                InlineKeyboardButton("❌ 거절", callback_data=f"rps:decline:{game_id}"),
            ]
        ]
    )


def rps_choice_keyboard(game_id):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✌️ 가위", callback_data=f"rps:pick:{game_id}:scissors"
                ),
                InlineKeyboardButton(
                    "✊ 바위", callback_data=f"rps:pick:{game_id}:rock"
                ),
                InlineKeyboardButton(
                    "✋ 보", callback_data=f"rps:pick:{game_id}:paper"
                ),
            ]
        ]
    )


def positive_int(text):
    try:
        number = int(text)
        return number if number > 0 else None
    except Exception:
        return None


async def help_cmd(update):
    text = (
        f"🎰 <b>{escape(BOT_TITLE)}</b>\n\n"
        "🎟 뽑기\n"
        "/내뽑기권\n"
        "/뽑기 또는 /뽑기 10\n"
        "/당첨내역\n"
        "/랭킹\n\n"
        "💎 게임포인트\n"
        "/내포인트\n"
        "/포인트랭킹\n"
        f"/뽑기권구매 1 — {TICKET_PRICE:,}P 사용\n\n"
        "🎮 가위바위보\n"
        "상대 메시지에 답장 후 /가위바위보 500\n"
        "/가위바위보전적\n"
        "/가위바위보랭킹\n\n"
        "관리자\n"
        "회원 메시지에 답장 후 /지급 5 또는 /회수 3\n"
        "회원 메시지에 답장 후 /포인트지급 1000 또는 /포인트회수 500\n\n"
        f"⛏ 일반 채팅은 {MINING_COOLDOWN_SECONDS}초마다 "
        "한 번씩 채굴 판정을 받습니다."
    )
    await update.effective_message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


async def tickets_cmd(update, callback=False):
    user = update.effective_user
    register(user)
    row = get_user(user.id)

    text = (
        f"🎟 <b>{uname(user)}님의 뽑기권</b>\n\n"
        f"보유: <b>{row['tickets']:,}장</b>\n"
        f"누적 당첨: <b>{row['total_points']:,}포인트</b>\n"
        f"총 뽑기: <b>{row['total_draws']:,}회</b>"
    )

    if callback:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
        )
    else:
        await update.effective_message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
        )


async def game_points_cmd(update, callback=False):
    user = update.effective_user
    register(user)
    row = get_user(user.id)

    text = (
        f"💎 <b>{uname(user)}님의 게임포인트</b>\n\n"
        f"보유: <b>{row['game_points']:,}P</b>\n"
        f"뽑기권 1장 가격: <b>{TICKET_PRICE:,}P</b>\n\n"
        "구매: <code>/뽑기권구매 1</code>"
    )

    if callback:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
        )
    else:
        await update.effective_message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
        )


def choose(count):
    names = [item[0] for item in REWARDS]
    weights = [item[2] for item in REWARDS]
    points = {item[0]: item[1] for item in REWARDS}
    return [
        (name, points[name])
        for name in random.choices(names, weights=weights, k=count)
    ]


async def draw_cmd(update, count, callback=False):
    user = update.effective_user
    register(user)

    if count < 1 or count > MAX_MULTI_DRAW:
        message = f"뽑기 횟수는 1~{MAX_MULTI_DRAW}회까지만 가능합니다."
        if callback:
            await update.callback_query.answer(message, show_alert=True)
        else:
            await update.effective_message.reply_text(message)
        return

    rewards = choose(count)

    try:
        remaining, total = perform_draws(user.id, rewards, count)
    except Exception as error:
        if callback:
            await update.callback_query.answer(str(error), show_alert=True)
        else:
            await update.effective_message.reply_text(str(error))
        return

    max_reward = max(item[1] for item in REWARDS)

    if count == 1:
        text = (
            f"🎰 <b>{escape(BOT_TITLE)}</b>\n\n"
            f"👤 {uname(user)}\n\n"
            "━━━━━━━━━━━━━━\n"
            f"🎁 <b>{escape(rewards[0][0])}</b>\n"
            "━━━━━━━━━━━━━━\n\n"
            f"🎟 남은 뽑기권: <b>{remaining:,}장</b>\n"
            f"🏆 최대 당첨금: <b>{max_reward:,}포인트</b>"
        )
    else:
        lines = [
            f"{index}. {escape(name)}"
            for index, (name, _) in enumerate(rewards, 1)
        ]
        text = (
            f"🎰 <b>{count}회 뽑기 완료!</b>\n\n"
            + "\n".join(lines)
            + "\n\n━━━━━━━━━━━━━━\n"
            f"💰 총 획득: <b>{total:,}포인트</b>\n"
            f"🎟 남은 뽑기권: <b>{remaining:,}장</b>\n"
            f"🏆 이번 최고 당첨: "
            f"<b>{max(points for _, points in rewards):,}포인트</b>"
        )

    target = (
        update.callback_query.message
        if callback
        else update.effective_message
    )

    if callback:
        await update.callback_query.answer()

    await target.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


async def buy_ticket_cmd(update, args):
    quantity = positive_int(args)

    if quantity is None:
        await update.effective_message.reply_text(
            "사용법: /뽑기권구매 1"
        )
        return

    if quantity > MAX_TICKET_PURCHASE:
        await update.effective_message.reply_text(
            f"한 번에 최대 {MAX_TICKET_PURCHASE}장까지 구매할 수 있습니다."
        )
        return

    user = update.effective_user
    register(user)

    try:
        remaining_points, tickets, cost = buy_tickets_with_points(
            user.id,
            quantity,
            TICKET_PRICE,
        )
    except Exception as error:
        await update.effective_message.reply_text(str(error))
        return

    await update.effective_message.reply_text(
        "✅ <b>뽑기권 구매 완료</b>\n\n"
        f"구매: <b>{quantity:,}장</b>\n"
        f"사용: <b>{cost:,}P</b>\n"
        f"남은 게임포인트: <b>{remaining_points:,}P</b>\n"
        f"보유 뽑기권: <b>{tickets:,}장</b>",
        parse_mode=ParseMode.HTML,
    )


async def point_ranking_cmd(update):
    rows = get_game_point_ranking(10)

    if not rows:
        await update.effective_message.reply_text(
            "아직 게임포인트 보유자가 없습니다."
        )
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = ["💎 <b>게임포인트 랭킹</b>\n"]

    for index, row in enumerate(rows, 1):
        rank = medals[index - 1] if index <= 3 else f"{index}."
        lines.append(
            f"{rank} {row_name(row)} — <b>{row['game_points']:,}P</b>"
        )

    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


async def admin_action(update, command, args):
    admin = update.effective_user

    if admin.id not in ADMIN_IDS:
        await update.effective_message.reply_text("관리자만 사용할 수 있습니다.")
        return

    message = update.effective_message
    target = None
    amount = None

    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user

        if target.is_bot:
            await message.reply_text("봇 계정에는 지급할 수 없습니다.")
            return

        register(target)
        amount = positive_int(args.strip())
    else:
        parts = args.split()

        if len(parts) == 2 and parts[0].startswith("@"):
            amount = positive_int(parts[1])
            target = find_user_by_username(parts[0])

    if not target or not amount:
        await message.reply_text(
            "사용법: 회원 메시지에 답장하고 /지급 5"
        )
        return

    if hasattr(target, "id"):
        target_id = target.id
        target_name = uname(target)
    else:
        target_id = int(target["user_id"])
        target_name = row_name(target)

    try:
        before, after = change_tickets(
            admin.id,
            target_id,
            amount if command == "지급" else -amount,
            update.effective_chat.id,
        )
    except Exception as error:
        await message.reply_text(str(error))
        return

    await message.reply_text(
        f"✅ <b>{target_name}</b>님 뽑기권 {amount:,}장 {command} 완료\n"
        f"{before:,}장 → <b>{after:,}장</b>",
        parse_mode=ParseMode.HTML,
    )


async def game_point_admin_action(update, command, args):
    admin = update.effective_user

    if admin.id not in ADMIN_IDS:
        await update.effective_message.reply_text("관리자만 사용할 수 있습니다.")
        return

    message = update.effective_message
    target = None
    amount = None

    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user

        if target.is_bot:
            await message.reply_text("봇 계정에는 지급할 수 없습니다.")
            return

        register(target)
        amount = positive_int(args.strip())
    else:
        parts = args.split()

        if len(parts) == 2 and parts[0].startswith("@"):
            amount = positive_int(parts[1])
            target = find_user_by_username(parts[0])

    if not target or not amount:
        await message.reply_text(
            "사용법: 회원 메시지에 답장하고 /포인트지급 1000"
        )
        return

    if hasattr(target, "id"):
        target_id = target.id
        target_name = uname(target)
    else:
        target_id = int(target["user_id"])
        target_name = row_name(target)

    delta = amount if command == "포인트지급" else -amount

    try:
        before, after = change_game_points(
            admin.id,
            target_id,
            delta,
            update.effective_chat.id,
        )
    except Exception as error:
        await message.reply_text(str(error))
        return

    action_text = "지급" if command == "포인트지급" else "회수"
    sign = "+" if delta > 0 else "-"

    await message.reply_text(
        f"💎 <b>게임포인트 {action_text} 완료</b>\n\n"
        f"대상: <b>{target_name}</b>\n"
        f"{action_text}: <b>{sign}{amount:,}P</b>\n"
        f"변경 전: <b>{before:,}P</b>\n"
        f"현재 보유: <b>{after:,}P</b>",
        parse_mode=ParseMode.HTML,
    )


async def ranking_cmd(update, callback=False):
    rows = get_ranking(10)

    if not rows:
        text = "아직 뽑기 기록이 없습니다."
    else:
        medals = ["🥇", "🥈", "🥉"]
        lines = [f"🏆 <b>{escape(BOT_TITLE)} 랭킹</b>\n"]

        for index, row in enumerate(rows, 1):
            rank = medals[index - 1] if index <= 3 else f"{index}."
            lines.append(
                f"{rank} {row_name(row)} — "
                f"<b>{row['total_points']:,}P</b> "
                f"({row['total_draws']:,}회)"
            )

        text = "\n".join(lines)

    target = (
        update.callback_query.message
        if callback
        else update.effective_message
    )

    if callback:
        await update.callback_query.answer()

    await target.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


async def history_cmd(update):
    user = update.effective_user
    register(user)
    rows = get_draw_history(user.id, 10)

    if not rows:
        await update.effective_message.reply_text(
            "아직 당첨 내역이 없습니다."
        )
        return

    lines = [f"📜 <b>{uname(user)}님의 최근 당첨 내역</b>\n"]
    lines.extend(
        f"{index}. {escape(row['reward_name'])}"
        for index, row in enumerate(rows, 1)
    )

    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


async def rps_challenge_cmd(update, args):
    message = update.effective_message
    challenger = update.effective_user

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply_text(
            "상대방 메시지에 답장한 뒤 /가위바위보 500 처럼 입력하세요."
        )
        return

    opponent = message.reply_to_message.from_user

    if opponent.is_bot:
        await message.reply_text(
            "봇에게는 가위바위보를 신청할 수 없습니다."
        )
        return

    if opponent.id == challenger.id:
        await message.reply_text(
            "자기 자신과는 가위바위보를 할 수 없습니다."
        )
        return

    bet = positive_int(args.strip())

    if bet is None:
        await message.reply_text(
            "사용법: 상대방 메시지에 답장하고 /가위바위보 500"
        )
        return

    if bet < MIN_RPS_BET or bet > MAX_RPS_BET:
        await message.reply_text(
            f"베팅은 {MIN_RPS_BET:,}P~{MAX_RPS_BET:,}P까지 가능합니다."
        )
        return

    register(challenger)
    register(opponent)

    if has_active_rps(challenger.id) or has_active_rps(opponent.id):
        await message.reply_text(
            "둘 중 한 명이 이미 가위바위보를 진행 중입니다."
        )
        return

    game_id = secrets.token_hex(4)

    try:
        create_rps_game(
            game_id=game_id,
            chat_id=update.effective_chat.id,
            challenger_id=challenger.id,
            opponent_id=opponent.id,
            bet=bet,
        )
    except Exception as error:
        await message.reply_text(str(error))
        return

    text = (
        "🎮 <b>가위바위보 도전!</b>\n\n"
        f"{uname(challenger)}님이 {uname(opponent)}님에게 도전했습니다.\n\n"
        f"💎 각자 베팅: <b>{bet:,}P</b>\n"
        f"🏆 승자 획득: <b>{bet * 2:,}P</b>\n\n"
        f"{uname(opponent)}님만 수락하거나 거절할 수 있습니다."
    )

    sent = await message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=rps_accept_keyboard(game_id),
    )
    set_rps_message_id(game_id, sent.message_id)


def rps_winner(choice_a, choice_b):
    if choice_a == choice_b:
        return 0

    wins = {
        "scissors": "paper",
        "rock": "scissors",
        "paper": "rock",
    }
    return 1 if wins[choice_a] == choice_b else 2


async def rps_stats_cmd(update):
    user = update.effective_user
    register(user)
    row = get_rps_stats(user.id)

    await update.effective_message.reply_text(
        f"📊 <b>{uname(user)}님의 가위바위보 전적</b>\n\n"
        f"총 게임: <b>{row['games']:,}회</b>\n"
        f"승리: <b>{row['wins']:,}회</b>\n"
        f"패배: <b>{row['losses']:,}회</b>\n"
        f"무승부: <b>{row['draws']:,}회</b>\n"
        f"순손익: <b>{row['net_points']:+,}P</b>",
        parse_mode=ParseMode.HTML,
    )


async def rps_ranking_cmd(update):
    rows = get_rps_ranking(10)

    if not rows:
        await update.effective_message.reply_text(
            "아직 가위바위보 기록이 없습니다."
        )
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 <b>가위바위보 랭킹</b>\n"]

    for index, row in enumerate(rows, 1):
        rank = medals[index - 1] if index <= 3 else f"{index}."
        lines.append(
            f"{rank} {row_name(row)} — "
            f"<b>{row['wins']:,}승</b> "
            f"({row['games']:,}전 / {row['net_points']:+,}P)"
        )

    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


async def rps_callback(update):
    query = update.callback_query
    parts = (query.data or "").split(":")

    if len(parts) < 3:
        await query.answer(
            "잘못된 게임 정보입니다.",
            show_alert=True,
        )
        return

    action = parts[1]
    game_id = parts[2]
    game = get_rps_game(game_id)

    if not game:
        await query.answer(
            "게임이 존재하지 않습니다.",
            show_alert=True,
        )
        return

    if query.message.chat_id != int(game["chat_id"]):
        await query.answer(
            "잘못된 채팅방입니다.",
            show_alert=True,
        )
        return

    user_id = query.from_user.id
    challenger_name = (
        f"@{escape(game['challenger_username'])}"
        if game["challenger_username"]
        else escape(game["challenger_display_name"])
    )
    opponent_name = (
        f"@{escape(game['opponent_username'])}"
        if game["opponent_username"]
        else escape(game["opponent_display_name"])
    )

    if action == "accept":
        try:
            bet = accept_rps_game(game_id, user_id)
        except Exception as error:
            await query.answer(str(error), show_alert=True)
            return

        await query.answer("도전을 수락했습니다.")
        await query.edit_message_text(
            "🎮 <b>가위바위보 시작!</b>\n\n"
            f"{challenger_name} VS {opponent_name}\n\n"
            f"💎 각자 <b>{bet:,}P</b> 차감 완료\n"
            f"🏆 승자 획득: <b>{bet * 2:,}P</b>\n\n"
            "두 사람 모두 아래 버튼에서 하나를 선택하세요.\n"
            "두 사람 모두 고를 때까지 선택은 공개되지 않습니다.",
            parse_mode=ParseMode.HTML,
            reply_markup=rps_choice_keyboard(game_id),
        )
        return

    if action == "decline":
        try:
            decline_rps_game(game_id, user_id)
        except Exception as error:
            await query.answer(str(error), show_alert=True)
            return

        await query.answer("도전을 거절했습니다.")
        await query.edit_message_text(
            "❌ <b>가위바위보 도전이 거절되었습니다.</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    if action == "pick":
        if len(parts) != 4:
            await query.answer(
                "잘못된 선택입니다.",
                show_alert=True,
            )
            return

        choice = parts[3]

        try:
            updated = save_rps_choice(game_id, user_id, choice)
        except Exception as error:
            await query.answer(str(error), show_alert=True)
            return

        await query.answer(
            f"{RPS_LABELS[choice]} 선택 완료!",
            show_alert=True,
        )

        challenger_choice = updated["challenger_choice"]
        opponent_choice = updated["opponent_choice"]

        if not challenger_choice or not opponent_choice:
            return

        result = rps_winner(challenger_choice, opponent_choice)

        if result == 0:
            winner_id = None
            result_text = (
                "🤝 <b>무승부!</b>\n"
                "각자 베팅한 게임포인트를 돌려받았습니다."
            )
        elif result == 1:
            winner_id = int(updated["challenger_id"])
            result_text = f"🏆 승자: <b>{challenger_name}</b>"
        else:
            winner_id = int(updated["opponent_id"])
            result_text = f"🏆 승자: <b>{opponent_name}</b>"

        pot = settle_rps_game(game_id, winner_id)

        text = (
            "🎮 <b>가위바위보 결과</b>\n\n"
            f"{challenger_name}: <b>{RPS_LABELS[challenger_choice]}</b>\n"
            f"{opponent_name}: <b>{RPS_LABELS[opponent_choice]}</b>\n\n"
            f"━━━━━━━━━━━━━━\n{result_text}"
        )

        if winner_id is not None:
            text += f"\n💎 획득 게임포인트: <b>{pot:,}P</b>"

        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
        )
        return

    await query.answer(
        "지원하지 않는 동작입니다.",
        show_alert=True,
    )


async def command_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_group(update):
        await update.effective_message.reply_text(
            "이 봇은 그룹에서만 사용할 수 있습니다."
        )
        return

    register(update.effective_user)
    match = COMMAND_RE.match(update.effective_message.text or "")

    if not match:
        return

    command = match.group("command")
    args = (match.group("args") or "").strip()

    if command in ("시작", "도움말", "등록"):
        await help_cmd(update)
    elif command == "내뽑기권":
        await tickets_cmd(update)
    elif command == "내포인트":
        await game_points_cmd(update)
    elif command == "뽑기":
        count = 1 if not args else positive_int(args)

        if count is None:
            await update.effective_message.reply_text(
                "사용법: /뽑기 또는 /뽑기 10"
            )
        else:
            await draw_cmd(update, count)
    elif command == "뽑기권구매":
        await buy_ticket_cmd(update, args)
    elif command in ("지급", "회수"):
        await admin_action(update, command, args)
    elif command in ("포인트지급", "포인트회수"):
        await game_point_admin_action(update, command, args)
    elif command == "랭킹":
        await ranking_cmd(update)
    elif command == "포인트랭킹":
        await point_ranking_cmd(update)
    elif command == "당첨내역":
        await history_cmd(update)
    elif command == "가위바위보":
        await rps_challenge_cmd(update, args)
    elif command == "가위바위보전적":
        await rps_stats_cmd(update)
    elif command == "가위바위보랭킹":
        await rps_ranking_cmd(update)


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_group(update):
        await update.callback_query.answer(
            "그룹에서만 사용할 수 있습니다.",
            show_alert=True,
        )
        return

    data = update.callback_query.data or ""

    if data.startswith("rps:"):
        await rps_callback(update)
    elif data == "tickets":
        await tickets_cmd(update, True)
    elif data == "gamepoints":
        await game_points_cmd(update, True)
    elif data == "ranking":
        await ranking_cmd(update, True)
    elif data.startswith("draw:"):
        await draw_cmd(
            update,
            int(data.split(":", 1)[1]),
            True,
        )


async def passive_register_and_mine(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_group(update) or not update.effective_user:
        return

    user = update.effective_user
    message = update.effective_message

    if user.is_bot or not message or not message.text:
        return

    register(user)

    # 명령어는 채굴 판정에서 제외
    if message.text.startswith("/"):
        return

    if not claim_mining_attempt(
        user.id,
        MINING_COOLDOWN_SECONDS,
    ):
        return

    if random.random() > MINING_CHANCE:
        return

    mined = random.randint(
        MINING_MIN_POINTS,
        MINING_MAX_POINTS,
    )
    balance = add_mined_points(user.id, mined)

    await message.reply_text(
        f"⛏ <b>{uname(user)}</b>님이 "
        f"<b>{mined:,}P</b>를 채굴했습니다!\n"
        f"💎 현재 보유: <b>{balance:,}P</b>",
        parse_mode=ParseMode.HTML,
    )


async def error_handler(update, context):
    logger.exception(
        "업데이트 처리 중 오류",
        exc_info=context.error,
    )


def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "Railway Variables에 BOT_TOKEN을 등록하세요."
        )

    if not ADMIN_IDS:
        raise RuntimeError(
            "Railway Variables에 ADMIN_IDS를 등록하세요."
        )

    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(COMMAND_RE),
            command_router,
        ),
        group=0,
    )

    app.add_handler(
        CallbackQueryHandler(callback_router),
        group=0,
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            passive_register_and_mine,
        ),
        group=1,
    )

    app.add_error_handler(error_handler)

    logger.info("봇 실행 시작")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
