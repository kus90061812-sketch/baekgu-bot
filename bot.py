import asyncio
import logging
import random
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.error import NetworkError, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters,
)

# =========================================================
# 반드시 아래 두 값만 수정하세요.
# =========================================================
TOKEN = "8999195481:AAGdiAM4k7szCVX8DlPQoa9mxBdY4RaX6Q4"
ADMIN_ID = 7936160142

# Railway Volume을 /data 경로에 연결했다면 그대로 사용됩니다.
# /data 폴더가 없는 환경에서는 현재 폴더에 bot.db가 생성됩니다.
DATA_DIR = Path("/data") if Path("/data").exists() else Path(".")
DB_PATH = DATA_DIR / "bot.db"

# 당첨 항목과 가중치
REWARDS = [
    (3_000, 50),
    (5_000, 28),
    (10_000, 15),
    (30_000, 5),
    (50_000, 2),
]

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("telegram-draw-bot")

db_lock = asyncio.Lock()


# =========================================================
# 데이터베이스
# =========================================================
def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_database() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with closing(connect_db()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                display_name TEXT NOT NULL,
                tickets INTEGER NOT NULL DEFAULT 0 CHECK(tickets >= 0),
                total_draws INTEGER NOT NULL DEFAULT 0,
                total_points INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_users_username
            ON users(username COLLATE NOCASE)
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS draw_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                reward INTEGER NOT NULL,
                remaining_tickets INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ticket_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER NOT NULL,
                target_user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.commit()


def normalize_username(username: Optional[str]) -> Optional[str]:
    if not username:
        return None
    return username.lstrip("@").strip().lower() or None


def save_or_update_user(
    user_id: int,
    username: Optional[str],
    display_name: str,
) -> None:
    normalized = normalize_username(username)

    with closing(connect_db()) as conn:
        conn.execute(
            """
            INSERT INTO users(user_id, username, display_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                display_name = excluded.display_name,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, normalized, display_name),
        )
        conn.commit()


def get_user_by_id(user_id: int) -> Optional[sqlite3.Row]:
    with closing(connect_db()) as conn:
        return conn.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()


def get_user_by_username(username: str) -> Optional[sqlite3.Row]:
    normalized = normalize_username(username)
    if not normalized:
        return None

    with closing(connect_db()) as conn:
        return conn.execute(
            """
            SELECT *
            FROM users
            WHERE username = ? COLLATE NOCASE
            """,
            (normalized,),
        ).fetchone()


def add_tickets(
    admin_id: int,
    target_user_id: int,
    amount: int,
    reason: str,
) -> int:
    with closing(connect_db()) as conn:
        conn.execute("BEGIN IMMEDIATE")

        row = conn.execute(
            "SELECT tickets FROM users WHERE user_id = ?",
            (target_user_id,),
        ).fetchone()

        if row is None:
            conn.rollback()
            raise ValueError("등록되지 않은 사용자입니다.")

        new_balance = row["tickets"] + amount
        if new_balance < 0:
            conn.rollback()
            raise ValueError("보유 뽑기권보다 많이 차감할 수 없습니다.")

        conn.execute(
            """
            UPDATE users
            SET tickets = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
            """,
            (new_balance, target_user_id),
        )

        conn.execute(
            """
            INSERT INTO ticket_logs(admin_id, target_user_id, amount, reason)
            VALUES (?, ?, ?, ?)
            """,
            (admin_id, target_user_id, amount, reason),
        )

        conn.commit()
        return new_balance


def perform_draw(user_id: int) -> tuple[int, int]:
    reward_values = [reward for reward, _ in REWARDS]
    reward_weights = [weight for _, weight in REWARDS]
    reward = random.choices(reward_values, weights=reward_weights, k=1)[0]

    with closing(connect_db()) as conn:
        conn.execute("BEGIN IMMEDIATE")

        row = conn.execute(
            """
            SELECT tickets, username
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()

        if row is None or row["tickets"] <= 0:
            conn.rollback()
            raise ValueError("뽑기권이 없습니다.")

        remaining = row["tickets"] - 1

        conn.execute(
            """
            UPDATE users
            SET tickets = ?,
                total_draws = total_draws + 1,
                total_points = total_points + ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
            """,
            (remaining, reward, user_id),
        )

        conn.execute(
            """
            INSERT INTO draw_logs(user_id, username, reward, remaining_tickets)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, row["username"], reward, remaining),
        )

        conn.commit()
        return reward, remaining


def get_global_stats() -> sqlite3.Row:
    with closing(connect_db()) as conn:
        return conn.execute(
            """
            SELECT
                COUNT(*) AS user_count,
                COALESCE(SUM(tickets), 0) AS ticket_count,
                COALESCE(SUM(total_draws), 0) AS draw_count,
                COALESCE(SUM(total_points), 0) AS point_count
            FROM users
            """
        ).fetchone()


def get_recent_draws(limit: int = 10) -> list[sqlite3.Row]:
    with closing(connect_db()) as conn:
        return conn.execute(
            """
            SELECT user_id, username, reward, remaining_tickets, created_at
            FROM draw_logs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


# =========================================================
# 메시지
# =========================================================
def user_label(row: sqlite3.Row) -> str:
    if row["username"]:
        return f"@{row['username']}"
    return row["display_name"]


def help_message(is_admin: bool) -> str:
    lines = [
        "🎰 포인트 뽑기 봇",
        "",
        "사용자 명령어",
        "• /시작",
        "• /도움말",
        "• /내뽑기권",
        "• /뽑기",
        "",
        "당첨 목록",
        "• 3,000포인트",
        "• 5,000포인트",
        "• 10,000포인트",
        "• 30,000포인트",
        "• 50,000포인트",
    ]

    if is_admin:
        lines.extend(
            [
                "",
                "관리자 명령어",
                "• /지급 사용자ID 수량",
                "• /지급 @username 수량",
                "• /차감 사용자ID 수량",
                "• /차감 @username 수량",
                "• /조회 사용자ID",
                "• /조회 @username",
                "• /전체통계",
                "• /최근당첨",
            ]
        )

    return "\n".join(lines)


def resolve_target(target_text: str) -> Optional[sqlite3.Row]:
    if target_text.startswith("@"):
        return get_user_by_username(target_text)

    try:
        user_id = int(target_text)
    except ValueError:
        return None

    return get_user_by_id(user_id)


async def send_reply(update: Update, text: str) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(text)


# =========================================================
# 명령 처리
# =========================================================
async def handle_start(update: Update) -> None:
    await send_reply(
        update,
        "✅ 포인트 뽑기 봇이 정상 작동 중입니다.\n"
        "/도움말 명령어로 사용법을 확인하세요.",
    )


async def handle_my_tickets(update: Update) -> None:
    user = update.effective_user
    row = get_user_by_id(user.id)

    tickets = row["tickets"] if row else 0
    draws = row["total_draws"] if row else 0
    points = row["total_points"] if row else 0

    await send_reply(
        update,
        "🎫 내 뽑기권\n\n"
        f"보유 뽑기권: {tickets:,}장\n"
        f"누적 뽑기: {draws:,}회\n"
        f"누적 당첨: {points:,}포인트",
    )


async def handle_draw(update: Update) -> None:
    user = update.effective_user

    async with db_lock:
        try:
            reward, remaining = perform_draw(user.id)
        except ValueError:
            await send_reply(
                update,
                "❌ 보유한 뽑기권이 없습니다.\n"
                "관리자에게 뽑기권 지급을 요청하세요.",
            )
            return

    await send_reply(
        update,
        "🎉 뽑기 결과\n\n"
        f"💰 {reward:,}포인트 당첨!\n"
        f"🎫 남은 뽑기권: {remaining:,}장",
    )


async def handle_ticket_change(
    update: Update,
    parts: list[str],
    is_add: bool,
) -> None:
    if update.effective_user.id != ADMIN_ID:
        await send_reply(update, "❌ 관리자만 사용할 수 있는 명령어입니다.")
        return

    command_name = "지급" if is_add else "차감"

    if len(parts) != 3:
        await send_reply(
            update,
            f"사용법: /{command_name} 사용자ID 수량\n"
            f"또는 /{command_name} @username 수량",
        )
        return

    target = resolve_target(parts[1])
    if target is None:
        await send_reply(
            update,
            "❌ 사용자를 찾을 수 없습니다.\n"
            "@username 지급은 해당 사용자가 먼저 봇에 메시지를 보낸 뒤 사용할 수 있습니다.",
        )
        return

    try:
        amount = int(parts[2].replace(",", ""))
    except ValueError:
        await send_reply(update, "❌ 수량은 숫자로 입력하세요.")
        return

    if amount <= 0:
        await send_reply(update, "❌ 수량은 1 이상이어야 합니다.")
        return

    signed_amount = amount if is_add else -amount

    async with db_lock:
        try:
            new_balance = add_tickets(
                admin_id=update.effective_user.id,
                target_user_id=target["user_id"],
                amount=signed_amount,
                reason=command_name,
            )
        except ValueError as exc:
            await send_reply(update, f"❌ {exc}")
            return

    await send_reply(
        update,
        f"✅ 뽑기권 {command_name} 완료\n\n"
        f"대상: {user_label(target)}\n"
        f"{command_name} 수량: {amount:,}장\n"
        f"현재 보유량: {new_balance:,}장",
    )


async def handle_lookup(update: Update, parts: list[str]) -> None:
    if update.effective_user.id != ADMIN_ID:
        await send_reply(update, "❌ 관리자만 사용할 수 있는 명령어입니다.")
        return

    if len(parts) != 2:
        await send_reply(
            update,
            "사용법: /조회 사용자ID\n또는 /조회 @username",
        )
        return

    target = resolve_target(parts[1])
    if target is None:
        await send_reply(update, "❌ 사용자를 찾을 수 없습니다.")
        return

    username_text = f"@{target['username']}" if target["username"] else "없음"

    await send_reply(
        update,
        "👤 사용자 조회\n\n"
        f"이름: {target['display_name']}\n"
        f"username: {username_text}\n"
        f"Telegram ID: {target['user_id']}\n"
        f"보유 뽑기권: {target['tickets']:,}장\n"
        f"누적 뽑기: {target['total_draws']:,}회\n"
        f"누적 당첨: {target['total_points']:,}포인트",
    )


async def handle_stats(update: Update) -> None:
    if update.effective_user.id != ADMIN_ID:
        await send_reply(update, "❌ 관리자만 사용할 수 있는 명령어입니다.")
        return

    stats = get_global_stats()

    await send_reply(
        update,
        "📊 전체 통계\n\n"
        f"등록 사용자: {stats['user_count']:,}명\n"
        f"남은 뽑기권: {stats['ticket_count']:,}장\n"
        f"누적 뽑기: {stats['draw_count']:,}회\n"
        f"누적 당첨: {stats['point_count']:,}포인트",
    )


async def handle_recent_draws(update: Update) -> None:
    if update.effective_user.id != ADMIN_ID:
        await send_reply(update, "❌ 관리자만 사용할 수 있는 명령어입니다.")
        return

    rows = get_recent_draws(10)
    if not rows:
        await send_reply(update, "최근 당첨 기록이 없습니다.")
        return

    lines = ["🧾 최근 당첨 10건", ""]
    for row in rows:
        name = f"@{row['username']}" if row["username"] else str(row["user_id"])
        lines.append(
            f"• {name} — {row['reward']:,}포인트 "
            f"(잔여 {row['remaining_tickets']:,}장)"
        )

    await send_reply(update, "\n".join(lines))


async def message_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.effective_message or not update.effective_user:
        return

    text = (update.effective_message.text or "").strip()
    if not text:
        return

    user = update.effective_user
    display_name = user.full_name or str(user.id)

    try:
        save_or_update_user(
            user_id=user.id,
            username=user.username,
            display_name=display_name,
        )
    except sqlite3.Error:
        logger.exception("사용자 저장 중 데이터베이스 오류")
        await send_reply(update, "❌ 사용자 정보를 저장하지 못했습니다.")
        return

    # 그룹에서 /명령어@봇아이디 형식으로 들어오는 경우도 처리
    first_token = text.split()[0]
    if "@" in first_token:
        command_without_bot = first_token.split("@", 1)[0]
        text = command_without_bot + text[len(first_token):]

    parts = text.split()
    command = parts[0]

    try:
        if command == "/시작":
            await handle_start(update)
        elif command == "/도움말":
            await send_reply(
                update,
                help_message(user.id == ADMIN_ID),
            )
        elif command == "/내뽑기권":
            await handle_my_tickets(update)
        elif command == "/뽑기":
            await handle_draw(update)
        elif command == "/지급":
            await handle_ticket_change(update, parts, is_add=True)
        elif command == "/차감":
            await handle_ticket_change(update, parts, is_add=False)
        elif command == "/조회":
            await handle_lookup(update, parts)
        elif command == "/전체통계":
            await handle_stats(update)
        elif command == "/최근당첨":
            await handle_recent_draws(update)
    except sqlite3.Error:
        logger.exception("데이터베이스 처리 오류")
        await send_reply(
            update,
            "❌ 데이터베이스 처리 중 오류가 발생했습니다.",
        )
    except TelegramError:
        logger.exception("텔레그램 API 오류")
    except Exception:
        logger.exception("예상하지 못한 오류")
        await send_reply(
            update,
            "❌ 명령어 처리 중 오류가 발생했습니다.",
        )


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if isinstance(context.error, NetworkError):
        logger.warning("네트워크 오류: %s", context.error)
        return

    logger.exception(
        "처리되지 않은 오류",
        exc_info=context.error,
    )


async def post_init(application: Application) -> None:
    bot = await application.bot.get_me()
    logger.info(
        "봇 실행 완료: @%s (ID: %s)",
        bot.username,
        bot.id,
    )


def validate_settings() -> None:
    if not TOKEN or TOKEN == "8999195481:AAGdiAM4k7szCVX8DlPQoa9mxBdY4RaX6Q4":
        raise RuntimeError(
            "bot.py 맨 위의 TOKEN에 실제 봇 토큰을 입력하세요."
        )

    if not isinstance(ADMIN_ID, int) or ADMIN_ID <= 0:
        raise RuntimeError(
            "ADMIN_ID에 관리자 Telegram 숫자 ID를 입력하세요."
        )


def main() -> None:
    token_value = TOKEN.strip()
    if ":" not in token_value or len(token_value) < 20:
        raise RuntimeError("TOKEN 형식이 잘못됐습니다. BotFather 토큰 전체를 따옴표 안에 넣으세요.")
    initialize_database()

    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    # 한글 슬래시 명령어를 직접 비교하므로 CommandHandler를 사용하지 않습니다.
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.UpdateType.EDITED_MESSAGE,
            message_handler,
        )
    )
    application.add_error_handler(error_handler)

    logger.info("데이터베이스 경로: %s", DB_PATH.resolve())
    logger.info("텔레그램 봇 시작 중...")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
