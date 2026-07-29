import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from config import DB_PATH


def now_ts():
    return int(datetime.now(timezone.utc).timestamp())


def now_iso():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _column_exists(conn, table, column):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


def init_db():
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                display_name TEXT NOT NULL,
                tickets INTEGER NOT NULL DEFAULT 0,
                total_points INTEGER NOT NULL DEFAULT 0,
                total_draws INTEGER NOT NULL DEFAULT 0,
                game_points INTEGER NOT NULL DEFAULT 0,
                last_mining_attempt INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS draw_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                reward_name TEXT NOT NULL,
                points INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS ticket_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                chat_id INTEGER,
                reason TEXT NOT NULL DEFAULT 'admin',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS game_point_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                reason TEXT NOT NULL,
                related_user_id INTEGER,
                game_id TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS rps_games (
                game_id TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                message_id INTEGER,
                challenger_id INTEGER NOT NULL,
                opponent_id INTEGER NOT NULL,
                bet INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                challenger_choice TEXT,
                opponent_choice TEXT,
                winner_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (challenger_id) REFERENCES users(user_id),
                FOREIGN KEY (opponent_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS rps_stats (
                user_id INTEGER PRIMARY KEY,
                wins INTEGER NOT NULL DEFAULT 0,
                losses INTEGER NOT NULL DEFAULT 0,
                draws INTEGER NOT NULL DEFAULT 0,
                games INTEGER NOT NULL DEFAULT 0,
                net_points INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE INDEX IF NOT EXISTS idx_draw_history_user
            ON draw_history(user_id, id DESC);

            CREATE INDEX IF NOT EXISTS idx_users_game_points
            ON users(game_points DESC);

            CREATE INDEX IF NOT EXISTS idx_rps_status
            ON rps_games(status);

            CREATE INDEX IF NOT EXISTS idx_rps_players
            ON rps_games(challenger_id, opponent_id);
            """
        )

        # 예전 users 테이블을 그대로 쓰는 경우 자동으로 새 컬럼 추가
        if not _column_exists(conn, "users", "game_points"):
            conn.execute(
                "ALTER TABLE users ADD COLUMN game_points INTEGER NOT NULL DEFAULT 0"
            )

        if not _column_exists(conn, "users", "last_mining_attempt"):
            conn.execute(
                "ALTER TABLE users ADD COLUMN last_mining_attempt INTEGER NOT NULL DEFAULT 0"
            )


def upsert_user(user_id, username, display_name):
    now = now_iso()
    with db() as conn:
        conn.execute(
            """
            INSERT INTO users (
                user_id, username, display_name, tickets,
                total_points, total_draws, game_points,
                last_mining_attempt, created_at, updated_at
            )
            VALUES (?, ?, ?, 0, 0, 0, 0, 0, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                display_name = excluded.display_name,
                updated_at = excluded.updated_at
            """,
            (int(user_id), username, display_name, now, now),
        )
        conn.execute(
            """
            INSERT INTO rps_stats (user_id)
            VALUES (?)
            ON CONFLICT(user_id) DO NOTHING
            """,
            (int(user_id),),
        )


def get_user(user_id):
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()
        if not row:
            raise ValueError("등록되지 않은 회원입니다.")
        return row


def find_user_by_username(username):
    clean = username.lstrip("@").lower()
    with db() as conn:
        return conn.execute(
            """
            SELECT *
            FROM users
            WHERE lower(username) = ?
            """,
            (clean,),
        ).fetchone()


def change_tickets(admin_id, user_id, amount, chat_id):
    amount = int(amount)
    user_id = int(user_id)

    with db() as conn:
        row = conn.execute(
            "SELECT tickets FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        if not row:
            raise ValueError("등록되지 않은 회원입니다.")

        before = int(row["tickets"])
        after = before + amount

        if after < 0:
            raise ValueError("보유 뽑기권보다 많이 회수할 수 없습니다.")

        conn.execute(
            """
            UPDATE users
            SET tickets = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (after, now_iso(), user_id),
        )

        conn.execute(
            """
            INSERT INTO ticket_logs (
                admin_id, user_id, amount, chat_id, reason, created_at
            )
            VALUES (?, ?, ?, ?, 'admin', ?)
            """,
            (admin_id, user_id, amount, chat_id, now_iso()),
        )

        return before, after


def perform_draws(user_id, rewards, count):
    user_id = int(user_id)
    count = int(count)

    with db() as conn:
        row = conn.execute(
            "SELECT tickets FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        if not row:
            raise ValueError("등록되지 않은 회원입니다.")

        tickets = int(row["tickets"])

        if tickets < count:
            raise ValueError(
                f"뽑기권이 부족합니다. 현재 {tickets:,}장 보유 중입니다."
            )

        total = sum(int(points) for _, points in rewards)
        remaining = tickets - count
        now = now_iso()

        conn.execute(
            """
            UPDATE users
            SET tickets = ?,
                total_points = total_points + ?,
                total_draws = total_draws + ?,
                updated_at = ?
            WHERE user_id = ?
            """,
            (remaining, total, count, now, user_id),
        )

        conn.executemany(
            """
            INSERT INTO draw_history (
                user_id, reward_name, points, created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            [
                (user_id, reward_name, int(points), now)
                for reward_name, points in rewards
            ],
        )

        return remaining, total


def get_ranking(limit=10):
    with db() as conn:
        return conn.execute(
            """
            SELECT user_id, username, display_name,
                   total_points, total_draws
            FROM users
            WHERE total_draws > 0
            ORDER BY total_points DESC, total_draws DESC, user_id ASC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()


def get_draw_history(user_id, limit=10):
    with db() as conn:
        return conn.execute(
            """
            SELECT reward_name, points, created_at
            FROM draw_history
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(user_id), int(limit)),
        ).fetchall()


def claim_mining_attempt(user_id, cooldown_seconds):
    user_id = int(user_id)
    now = now_ts()

    with db() as conn:
        row = conn.execute(
            """
            SELECT last_mining_attempt
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()

        if not row:
            return False

        last_attempt = int(row["last_mining_attempt"] or 0)

        if now - last_attempt < int(cooldown_seconds):
            return False

        conn.execute(
            """
            UPDATE users
            SET last_mining_attempt = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (now, now_iso(), user_id),
        )
        return True


def add_mined_points(user_id, amount):
    amount = int(amount)

    with db() as conn:
        conn.execute(
            """
            UPDATE users
            SET game_points = game_points + ?, updated_at = ?
            WHERE user_id = ?
            """,
            (amount, now_iso(), int(user_id)),
        )

        conn.execute(
            """
            INSERT INTO game_point_logs (
                user_id, amount, reason, created_at
            )
            VALUES (?, ?, 'mining', ?)
            """,
            (int(user_id), amount, now_iso()),
        )

        row = conn.execute(
            "SELECT game_points FROM users WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()

        return int(row["game_points"])



def change_game_points(admin_id, user_id, amount, chat_id):
    amount = int(amount)
    user_id = int(user_id)

    with db() as conn:
        row = conn.execute(
            """
            SELECT game_points
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()

        if not row:
            raise ValueError("등록되지 않은 회원입니다.")

        before = int(row["game_points"])
        after = before + amount

        if after < 0:
            raise ValueError("보유 게임포인트보다 많이 회수할 수 없습니다.")

        conn.execute(
            """
            UPDATE users
            SET game_points = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (after, now_iso(), user_id),
        )

        conn.execute(
            """
            INSERT INTO game_point_logs (
                user_id,
                amount,
                reason,
                related_user_id,
                game_id,
                created_at
            )
            VALUES (?, ?, ?, ?, NULL, ?)
            """,
            (
                user_id,
                amount,
                "admin_grant" if amount > 0 else "admin_revoke",
                int(admin_id),
                now_iso(),
            ),
        )

        return before, after


def buy_tickets_with_points(user_id, quantity, ticket_price):
    user_id = int(user_id)
    quantity = int(quantity)
    cost = quantity * int(ticket_price)

    if quantity < 1:
        raise ValueError("구매 수량은 1장 이상이어야 합니다.")

    with db() as conn:
        row = conn.execute(
            """
            SELECT game_points, tickets
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()

        if not row:
            raise ValueError("등록되지 않은 회원입니다.")

        if int(row["game_points"]) < cost:
            raise ValueError(
                f"게임포인트가 부족합니다. 필요: {cost:,}P / "
                f"보유: {int(row['game_points']):,}P"
            )

        conn.execute(
            """
            UPDATE users
            SET game_points = game_points - ?,
                tickets = tickets + ?,
                updated_at = ?
            WHERE user_id = ?
            """,
            (cost, quantity, now_iso(), user_id),
        )

        conn.execute(
            """
            INSERT INTO game_point_logs (
                user_id, amount, reason, created_at
            )
            VALUES (?, ?, 'ticket_purchase', ?)
            """,
            (user_id, -cost, now_iso()),
        )

        updated = conn.execute(
            """
            SELECT game_points, tickets
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()

        return int(updated["game_points"]), int(updated["tickets"]), cost


def get_game_point_ranking(limit=10):
    with db() as conn:
        return conn.execute(
            """
            SELECT user_id, username, display_name, game_points
            FROM users
            WHERE game_points > 0
            ORDER BY game_points DESC, user_id ASC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()


def has_active_rps(user_id):
    with db() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM rps_games
            WHERE status IN ('pending', 'playing')
              AND (challenger_id = ? OR opponent_id = ?)
            LIMIT 1
            """,
            (int(user_id), int(user_id)),
        ).fetchone()
        return bool(row)


def create_rps_game(
    game_id,
    chat_id,
    challenger_id,
    opponent_id,
    bet,
    message_id=None,
):
    bet = int(bet)

    with db() as conn:
        active = conn.execute(
            """
            SELECT 1
            FROM rps_games
            WHERE status IN ('pending', 'playing')
              AND (
                challenger_id IN (?, ?)
                OR opponent_id IN (?, ?)
              )
            LIMIT 1
            """,
            (
                int(challenger_id),
                int(opponent_id),
                int(challenger_id),
                int(opponent_id),
            ),
        ).fetchone()

        if active:
            raise ValueError("둘 중 한 명이 이미 가위바위보를 진행 중입니다.")

        challenger = conn.execute(
            """
            SELECT game_points
            FROM users
            WHERE user_id = ?
            """,
            (int(challenger_id),),
        ).fetchone()

        if not challenger or int(challenger["game_points"]) < bet:
            raise ValueError("도전자의 게임포인트가 부족합니다.")

        now = now_iso()
        conn.execute(
            """
            INSERT INTO rps_games (
                game_id, chat_id, message_id,
                challenger_id, opponent_id, bet,
                status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                game_id,
                int(chat_id),
                message_id,
                int(challenger_id),
                int(opponent_id),
                bet,
                now,
                now,
            ),
        )


def set_rps_message_id(game_id, message_id):
    with db() as conn:
        conn.execute(
            """
            UPDATE rps_games
            SET message_id = ?, updated_at = ?
            WHERE game_id = ?
            """,
            (int(message_id), now_iso(), game_id),
        )


def get_rps_game(game_id):
    with db() as conn:
        return conn.execute(
            """
            SELECT g.*,
                   cu.username AS challenger_username,
                   cu.display_name AS challenger_display_name,
                   ou.username AS opponent_username,
                   ou.display_name AS opponent_display_name
            FROM rps_games g
            JOIN users cu ON cu.user_id = g.challenger_id
            JOIN users ou ON ou.user_id = g.opponent_id
            WHERE g.game_id = ?
            """,
            (game_id,),
        ).fetchone()


def accept_rps_game(game_id, opponent_id):
    with db() as conn:
        game = conn.execute(
            "SELECT * FROM rps_games WHERE game_id = ?",
            (game_id,),
        ).fetchone()

        if not game:
            raise ValueError("게임이 존재하지 않습니다.")

        if game["status"] != "pending":
            raise ValueError("이미 처리된 게임입니다.")

        if int(game["opponent_id"]) != int(opponent_id):
            raise ValueError("도전받은 상대방만 수락할 수 있습니다.")

        bet = int(game["bet"])

        challenger = conn.execute(
            "SELECT game_points FROM users WHERE user_id = ?",
            (int(game["challenger_id"]),),
        ).fetchone()
        opponent = conn.execute(
            "SELECT game_points FROM users WHERE user_id = ?",
            (int(game["opponent_id"]),),
        ).fetchone()

        if not challenger or int(challenger["game_points"]) < bet:
            raise ValueError("도전자의 게임포인트가 부족합니다.")

        if not opponent or int(opponent["game_points"]) < bet:
            raise ValueError("상대방의 게임포인트가 부족합니다.")

        now = now_iso()

        conn.execute(
            """
            UPDATE users
            SET game_points = game_points - ?, updated_at = ?
            WHERE user_id IN (?, ?)
            """,
            (
                bet,
                now,
                int(game["challenger_id"]),
                int(game["opponent_id"]),
            ),
        )

        conn.executemany(
            """
            INSERT INTO game_point_logs (
                user_id, amount, reason, related_user_id,
                game_id, created_at
            )
            VALUES (?, ?, 'rps_bet', ?, ?, ?)
            """,
            [
                (
                    int(game["challenger_id"]),
                    -bet,
                    int(game["opponent_id"]),
                    game_id,
                    now,
                ),
                (
                    int(game["opponent_id"]),
                    -bet,
                    int(game["challenger_id"]),
                    game_id,
                    now,
                ),
            ],
        )

        conn.execute(
            """
            UPDATE rps_games
            SET status = 'playing', updated_at = ?
            WHERE game_id = ?
            """,
            (now, game_id),
        )

        return bet


def decline_rps_game(game_id, opponent_id):
    with db() as conn:
        game = conn.execute(
            "SELECT * FROM rps_games WHERE game_id = ?",
            (game_id,),
        ).fetchone()

        if not game:
            raise ValueError("게임이 존재하지 않습니다.")

        if game["status"] != "pending":
            raise ValueError("이미 처리된 게임입니다.")

        if int(game["opponent_id"]) != int(opponent_id):
            raise ValueError("도전받은 상대방만 거절할 수 있습니다.")

        conn.execute(
            """
            UPDATE rps_games
            SET status = 'declined', updated_at = ?
            WHERE game_id = ?
            """,
            (now_iso(), game_id),
        )


def save_rps_choice(game_id, user_id, choice):
    if choice not in ("scissors", "rock", "paper"):
        raise ValueError("잘못된 선택입니다.")

    with db() as conn:
        game = conn.execute(
            "SELECT * FROM rps_games WHERE game_id = ?",
            (game_id,),
        ).fetchone()

        if not game:
            raise ValueError("게임이 존재하지 않습니다.")

        if game["status"] != "playing":
            raise ValueError("진행 중인 게임이 아닙니다.")

        user_id = int(user_id)

        if user_id == int(game["challenger_id"]):
            column = "challenger_choice"
        elif user_id == int(game["opponent_id"]):
            column = "opponent_choice"
        else:
            raise ValueError("게임 참가자만 선택할 수 있습니다.")

        if game[column]:
            raise ValueError("이미 선택했습니다.")

        conn.execute(
            f"""
            UPDATE rps_games
            SET {column} = ?, updated_at = ?
            WHERE game_id = ?
            """,
            (choice, now_iso(), game_id),
        )

        return conn.execute(
            "SELECT * FROM rps_games WHERE game_id = ?",
            (game_id,),
        ).fetchone()


def settle_rps_game(game_id, winner_id):
    with db() as conn:
        game = conn.execute(
            "SELECT * FROM rps_games WHERE game_id = ?",
            (game_id,),
        ).fetchone()

        if not game:
            raise ValueError("게임이 존재하지 않습니다.")

        if game["status"] != "playing":
            raise ValueError("이미 종료된 게임입니다.")

        challenger_id = int(game["challenger_id"])
        opponent_id = int(game["opponent_id"])
        bet = int(game["bet"])
        pot = bet * 2
        now = now_iso()

        if winner_id is None:
            conn.execute(
                """
                UPDATE users
                SET game_points = game_points + ?, updated_at = ?
                WHERE user_id IN (?, ?)
                """,
                (bet, now, challenger_id, opponent_id),
            )

            conn.executemany(
                """
                INSERT INTO game_point_logs (
                    user_id, amount, reason, related_user_id,
                    game_id, created_at
                )
                VALUES (?, ?, 'rps_draw_refund', ?, ?, ?)
                """,
                [
                    (challenger_id, bet, opponent_id, game_id, now),
                    (opponent_id, bet, challenger_id, game_id, now),
                ],
            )

            for uid in (challenger_id, opponent_id):
                conn.execute(
                    """
                    INSERT INTO rps_stats (user_id, draws, games)
                    VALUES (?, 1, 1)
                    ON CONFLICT(user_id) DO UPDATE SET
                        draws = draws + 1,
                        games = games + 1
                    """,
                    (uid,),
                )

            status = "draw"
        else:
            winner_id = int(winner_id)

            if winner_id not in (challenger_id, opponent_id):
                raise ValueError("승자 정보가 올바르지 않습니다.")

            loser_id = opponent_id if winner_id == challenger_id else challenger_id

            conn.execute(
                """
                UPDATE users
                SET game_points = game_points + ?, updated_at = ?
                WHERE user_id = ?
                """,
                (pot, now, winner_id),
            )

            conn.execute(
                """
                INSERT INTO game_point_logs (
                    user_id, amount, reason, related_user_id,
                    game_id, created_at
                )
                VALUES (?, ?, 'rps_win', ?, ?, ?)
                """,
                (winner_id, pot, loser_id, game_id, now),
            )

            conn.execute(
                """
                INSERT INTO rps_stats (
                    user_id, wins, games, net_points
                )
                VALUES (?, 1, 1, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    wins = wins + 1,
                    games = games + 1,
                    net_points = net_points + excluded.net_points
                """,
                (winner_id, bet),
            )

            conn.execute(
                """
                INSERT INTO rps_stats (
                    user_id, losses, games, net_points
                )
                VALUES (?, 1, 1, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    losses = losses + 1,
                    games = games + 1,
                    net_points = net_points + excluded.net_points
                """,
                (loser_id, -bet),
            )

            status = "finished"

        conn.execute(
            """
            UPDATE rps_games
            SET status = ?, winner_id = ?, updated_at = ?
            WHERE game_id = ?
            """,
            (status, winner_id, now, game_id),
        )

        return pot


def get_rps_stats(user_id):
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM rps_stats WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()

        if row:
            return row

        conn.execute(
            "INSERT INTO rps_stats (user_id) VALUES (?)",
            (int(user_id),),
        )

        return conn.execute(
            "SELECT * FROM rps_stats WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()


def get_rps_ranking(limit=10):
    with db() as conn:
        return conn.execute(
            """
            SELECT s.*, u.username, u.display_name
            FROM rps_stats s
            JOIN users u ON u.user_id = s.user_id
            WHERE s.games > 0
            ORDER BY s.wins DESC, s.net_points DESC, s.games ASC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
