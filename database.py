from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

import psycopg
from psycopg.rows import dict_row

from config import DATABASE_URL


KST = timezone(timedelta(hours=9))


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def today_kst():
    return datetime.now(KST).date().isoformat()


@contextmanager
def db():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL이 없습니다. Railway 봇 서비스 Variables에 "
            "Postgres DATABASE_URL을 연결하세요."
        )

    conn = psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        connect_timeout=30,
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    statements = [
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            display_name TEXT NOT NULL,
            tickets BIGINT NOT NULL DEFAULT 0,
            total_points BIGINT NOT NULL DEFAULT 0,
            total_draws BIGINT NOT NULL DEFAULT 0,
            game_points BIGINT NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS draw_history (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(user_id),
            reward_name TEXT NOT NULL,
            points BIGINT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS ticket_logs (
            id BIGSERIAL PRIMARY KEY,
            admin_id BIGINT,
            user_id BIGINT NOT NULL,
            amount BIGINT NOT NULL,
            chat_id BIGINT,
            reason TEXT NOT NULL DEFAULT 'admin',
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS game_point_logs (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            amount BIGINT NOT NULL,
            reason TEXT NOT NULL,
            related_user_id BIGINT,
            game_id TEXT,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS attendance (
            user_id BIGINT NOT NULL REFERENCES users(user_id),
            attendance_date TEXT NOT NULL,
            reward BIGINT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, attendance_date)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS rps_games (
            game_id TEXT PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            message_id BIGINT,
            challenger_id BIGINT NOT NULL REFERENCES users(user_id),
            opponent_id BIGINT NOT NULL REFERENCES users(user_id),
            bet BIGINT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            challenger_choice TEXT,
            opponent_choice TEXT,
            winner_id BIGINT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS rps_stats (
            user_id BIGINT PRIMARY KEY REFERENCES users(user_id),
            wins BIGINT NOT NULL DEFAULT 0,
            losses BIGINT NOT NULL DEFAULT 0,
            draws BIGINT NOT NULL DEFAULT 0,
            games BIGINT NOT NULL DEFAULT 0,
            net_points BIGINT NOT NULL DEFAULT 0
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS point_drops (
            drop_id TEXT PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            message_id BIGINT,
            creator_id BIGINT NOT NULL,
            total_points BIGINT NOT NULL,
            max_claims BIGINT NOT NULL,
            points_per_claim BIGINT NOT NULL,
            claimed_count BIGINT NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS point_drop_claims (
            drop_id TEXT NOT NULL REFERENCES point_drops(drop_id) ON DELETE CASCADE,
            user_id BIGINT NOT NULL REFERENCES users(user_id),
            points BIGINT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (drop_id, user_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_draw_history_user
        ON draw_history(user_id, id DESC)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_users_game_points
        ON users(game_points DESC)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_rps_status
        ON rps_games(status)
        """,
    ]

    with db() as conn:
        for statement in statements:
            conn.execute(statement)


def upsert_user(user_id, username, display_name):
    now = now_iso()
    with db() as conn:
        conn.execute(
            """
            INSERT INTO users (
                user_id, username, display_name, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s)
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
            VALUES (%s)
            ON CONFLICT(user_id) DO NOTHING
            """,
            (int(user_id),),
        )


def get_user(user_id):
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE user_id = %s",
            (int(user_id),),
        ).fetchone()

        if not row:
            raise ValueError("등록되지 않은 회원입니다.")

        return row


def find_user_by_username(username):
    clean = username.lstrip("@").lower()

    with db() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE lower(username) = %s",
            (clean,),
        ).fetchone()


def change_tickets(admin_id, user_id, amount, chat_id):
    amount = int(amount)
    user_id = int(user_id)

    with db() as conn:
        row = conn.execute(
            "SELECT tickets FROM users WHERE user_id = %s FOR UPDATE",
            (user_id,),
        ).fetchone()

        if not row:
            raise ValueError("등록되지 않은 회원입니다.")

        before = int(row["tickets"])
        after = before + amount

        if after < 0:
            raise ValueError("보유 뽑기권보다 많이 회수할 수 없습니다.")

        now = now_iso()

        conn.execute(
            """
            UPDATE users
            SET tickets = %s, updated_at = %s
            WHERE user_id = %s
            """,
            (after, now, user_id),
        )
        conn.execute(
            """
            INSERT INTO ticket_logs (
                admin_id, user_id, amount, chat_id, reason, created_at
            )
            VALUES (%s, %s, %s, %s, 'admin', %s)
            """,
            (int(admin_id), user_id, amount, int(chat_id), now),
        )

        return before, after


def perform_draws(user_id, rewards, count):
    user_id = int(user_id)
    count = int(count)

    with db() as conn:
        row = conn.execute(
            "SELECT tickets FROM users WHERE user_id = %s FOR UPDATE",
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
            SET tickets = %s,
                total_points = total_points + %s,
                total_draws = total_draws + %s,
                updated_at = %s
            WHERE user_id = %s
            """,
            (remaining, total, count, now, user_id),
        )

        # psycopg3 Connection에는 executemany가 없으므로 개별 실행합니다.
        for name, points in rewards:
            conn.execute(
                """
                INSERT INTO draw_history (
                    user_id, reward_name, points, created_at
                )
                VALUES (%s, %s, %s, %s)
                """,
                (user_id, name, int(points), now),
            )

        return remaining, total


def get_ranking(limit=10):
    with db() as conn:
        return conn.execute(
            """
            SELECT user_id, username, display_name, total_points, total_draws
            FROM users
            WHERE total_draws > 0
            ORDER BY total_points DESC, total_draws DESC, user_id ASC
            LIMIT %s
            """,
            (int(limit),),
        ).fetchall()


def get_draw_history(user_id, limit=10):
    with db() as conn:
        return conn.execute(
            """
            SELECT reward_name, points, created_at
            FROM draw_history
            WHERE user_id = %s
            ORDER BY id DESC
            LIMIT %s
            """,
            (int(user_id), int(limit)),
        ).fetchall()


def add_mined_points(user_id, amount):
    user_id = int(user_id)
    amount = int(amount)

    with db() as conn:
        row = conn.execute(
            "SELECT game_points FROM users WHERE user_id = %s FOR UPDATE",
            (user_id,),
        ).fetchone()

        if not row:
            raise ValueError("등록되지 않은 회원입니다.")

        now = now_iso()
        balance = int(row["game_points"]) + amount

        conn.execute(
            """
            UPDATE users
            SET game_points = %s, updated_at = %s
            WHERE user_id = %s
            """,
            (balance, now, user_id),
        )
        conn.execute(
            """
            INSERT INTO game_point_logs (
                user_id, amount, reason, created_at
            )
            VALUES (%s, %s, 'mining', %s)
            """,
            (user_id, amount, now),
        )

        return balance


def change_game_points(admin_id, user_id, amount, chat_id):
    amount = int(amount)
    user_id = int(user_id)

    with db() as conn:
        row = conn.execute(
            "SELECT game_points FROM users WHERE user_id = %s FOR UPDATE",
            (user_id,),
        ).fetchone()

        if not row:
            raise ValueError("등록되지 않은 회원입니다.")

        before = int(row["game_points"])
        after = before + amount

        if after < 0:
            raise ValueError("보유 게임포인트보다 많이 회수할 수 없습니다.")

        now = now_iso()

        conn.execute(
            """
            UPDATE users
            SET game_points = %s, updated_at = %s
            WHERE user_id = %s
            """,
            (after, now, user_id),
        )
        conn.execute(
            """
            INSERT INTO game_point_logs (
                user_id, amount, reason, related_user_id, created_at
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                user_id,
                amount,
                "admin_grant" if amount > 0 else "admin_revoke",
                int(admin_id),
                now,
            ),
        )

        return before, after


def claim_attendance(user_id, reward):
    user_id = int(user_id)
    reward = int(reward)
    date_key = today_kst()
    now = now_iso()

    with db() as conn:
        exists = conn.execute(
            """
            SELECT 1
            FROM attendance
            WHERE user_id = %s AND attendance_date = %s
            """,
            (user_id, date_key),
        ).fetchone()

        if exists:
            return False, None

        conn.execute(
            """
            INSERT INTO attendance (
                user_id, attendance_date, reward, created_at
            )
            VALUES (%s, %s, %s, %s)
            """,
            (user_id, date_key, reward, now),
        )
        conn.execute(
            """
            UPDATE users
            SET game_points = game_points + %s, updated_at = %s
            WHERE user_id = %s
            """,
            (reward, now, user_id),
        )
        conn.execute(
            """
            INSERT INTO game_point_logs (
                user_id, amount, reason, created_at
            )
            VALUES (%s, %s, 'attendance', %s)
            """,
            (user_id, reward, now),
        )

        balance = conn.execute(
            "SELECT game_points FROM users WHERE user_id = %s",
            (user_id,),
        ).fetchone()

        return True, int(balance["game_points"])


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
            WHERE user_id = %s
            FOR UPDATE
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

        now = now_iso()

        conn.execute(
            """
            UPDATE users
            SET game_points = game_points - %s,
                tickets = tickets + %s,
                updated_at = %s
            WHERE user_id = %s
            """,
            (cost, quantity, now, user_id),
        )
        conn.execute(
            """
            INSERT INTO game_point_logs (
                user_id, amount, reason, created_at
            )
            VALUES (%s, %s, 'ticket_purchase', %s)
            """,
            (user_id, -cost, now),
        )

        updated = conn.execute(
            """
            SELECT game_points, tickets
            FROM users
            WHERE user_id = %s
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
            LIMIT %s
            """,
            (int(limit),),
        ).fetchall()


def has_active_rps(user_id):
    with db() as conn:
        return bool(
            conn.execute(
                """
                SELECT 1
                FROM rps_games
                WHERE status IN ('pending', 'playing')
                  AND (challenger_id = %s OR opponent_id = %s)
                LIMIT 1
                """,
                (int(user_id), int(user_id)),
            ).fetchone()
        )


def create_rps_game(
    game_id,
    chat_id,
    challenger_id,
    opponent_id,
    bet,
    message_id=None,
):
    challenger_id = int(challenger_id)
    opponent_id = int(opponent_id)
    bet = int(bet)

    with db() as conn:
        active = conn.execute(
            """
            SELECT 1
            FROM rps_games
            WHERE status IN ('pending', 'playing')
              AND (
                challenger_id IN (%s, %s)
                OR opponent_id IN (%s, %s)
              )
            LIMIT 1
            """,
            (
                challenger_id,
                opponent_id,
                challenger_id,
                opponent_id,
            ),
        ).fetchone()

        if active:
            raise ValueError("둘 중 한 명이 이미 가위바위보를 진행 중입니다.")

        challenger = conn.execute(
            "SELECT game_points FROM users WHERE user_id = %s",
            (challenger_id,),
        ).fetchone()

        if not challenger or int(challenger["game_points"]) < bet:
            raise ValueError("도전자의 게임포인트가 부족합니다.")

        now = now_iso()

        conn.execute(
            """
            INSERT INTO rps_games (
                game_id, chat_id, message_id, challenger_id,
                opponent_id, bet, status, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s, %s)
            """,
            (
                game_id,
                int(chat_id),
                message_id,
                challenger_id,
                opponent_id,
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
            SET message_id = %s, updated_at = %s
            WHERE game_id = %s
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
            WHERE g.game_id = %s
            """,
            (game_id,),
        ).fetchone()


def accept_rps_game(game_id, opponent_id):
    opponent_id = int(opponent_id)

    with db() as conn:
        game = conn.execute(
            "SELECT * FROM rps_games WHERE game_id = %s FOR UPDATE",
            (game_id,),
        ).fetchone()

        if not game:
            raise ValueError("게임이 존재하지 않습니다.")

        if game["status"] != "pending":
            raise ValueError("이미 처리된 게임입니다.")

        if int(game["opponent_id"]) != opponent_id:
            raise ValueError("도전받은 상대방만 수락할 수 있습니다.")

        challenger_id = int(game["challenger_id"])
        game_opponent_id = int(game["opponent_id"])
        bet = int(game["bet"])

        challenger = conn.execute(
            "SELECT game_points FROM users WHERE user_id = %s FOR UPDATE",
            (challenger_id,),
        ).fetchone()
        opponent = conn.execute(
            "SELECT game_points FROM users WHERE user_id = %s FOR UPDATE",
            (game_opponent_id,),
        ).fetchone()

        if not challenger or int(challenger["game_points"]) < bet:
            raise ValueError("도전자의 게임포인트가 부족합니다.")

        if not opponent or int(opponent["game_points"]) < bet:
            raise ValueError("상대방의 게임포인트가 부족합니다.")

        now = now_iso()

        conn.execute(
            """
            UPDATE users
            SET game_points = game_points - %s, updated_at = %s
            WHERE user_id IN (%s, %s)
            """,
            (bet, now, challenger_id, game_opponent_id),
        )

        # Connection.executemany 오류를 막기 위해 개별 INSERT로 처리합니다.
        conn.execute(
            """
            INSERT INTO game_point_logs (
                user_id, amount, reason, related_user_id, game_id, created_at
            )
            VALUES (%s, %s, 'rps_bet', %s, %s, %s)
            """,
            (
                challenger_id,
                -bet,
                game_opponent_id,
                game_id,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO game_point_logs (
                user_id, amount, reason, related_user_id, game_id, created_at
            )
            VALUES (%s, %s, 'rps_bet', %s, %s, %s)
            """,
            (
                game_opponent_id,
                -bet,
                challenger_id,
                game_id,
                now,
            ),
        )

        conn.execute(
            """
            UPDATE rps_games
            SET status = 'playing', updated_at = %s
            WHERE game_id = %s
            """,
            (now, game_id),
        )

        return bet


def decline_rps_game(game_id, opponent_id):
    opponent_id = int(opponent_id)

    with db() as conn:
        game = conn.execute(
            "SELECT * FROM rps_games WHERE game_id = %s FOR UPDATE",
            (game_id,),
        ).fetchone()

        if not game:
            raise ValueError("게임이 존재하지 않습니다.")

        if game["status"] != "pending":
            raise ValueError("이미 처리된 게임입니다.")

        if int(game["opponent_id"]) != opponent_id:
            raise ValueError("도전받은 상대방만 거절할 수 있습니다.")

        conn.execute(
            """
            UPDATE rps_games
            SET status = 'declined', updated_at = %s
            WHERE game_id = %s
            """,
            (now_iso(), game_id),
        )


def save_rps_choice(game_id, user_id, choice):
    if choice not in ("scissors", "rock", "paper"):
        raise ValueError("잘못된 선택입니다.")

    user_id = int(user_id)

    with db() as conn:
        game = conn.execute(
            "SELECT * FROM rps_games WHERE game_id = %s FOR UPDATE",
            (game_id,),
        ).fetchone()

        if not game:
            raise ValueError("게임이 존재하지 않습니다.")

        if game["status"] != "playing":
            raise ValueError("진행 중인 게임이 아닙니다.")

        if user_id == int(game["challenger_id"]):
            column = "challenger_choice"
        elif user_id == int(game["opponent_id"]):
            column = "opponent_choice"
        else:
            raise ValueError("게임 참가자만 선택할 수 있습니다.")

        if game[column]:
            raise ValueError("이미 선택했습니다.")

        # column은 위에서 두 값 중 하나로만 정해지므로 안전합니다.
        conn.execute(
            f"""
            UPDATE rps_games
            SET {column} = %s, updated_at = %s
            WHERE game_id = %s
            """,
            (choice, now_iso(), game_id),
        )

        return conn.execute(
            "SELECT * FROM rps_games WHERE game_id = %s",
            (game_id,),
        ).fetchone()


def settle_rps_game(game_id, winner_id):
    with db() as conn:
        game = conn.execute(
            "SELECT * FROM rps_games WHERE game_id = %s FOR UPDATE",
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
                SET game_points = game_points + %s, updated_at = %s
                WHERE user_id IN (%s, %s)
                """,
                (bet, now, challenger_id, opponent_id),
            )

            for user_id in (challenger_id, opponent_id):
                conn.execute(
                    """
                    INSERT INTO rps_stats (user_id, draws, games)
                    VALUES (%s, 1, 1)
                    ON CONFLICT(user_id) DO UPDATE SET
                        draws = rps_stats.draws + 1,
                        games = rps_stats.games + 1
                    """,
                    (user_id,),
                )
                conn.execute(
                    """
                    INSERT INTO game_point_logs (
                        user_id, amount, reason,
                        related_user_id, game_id, created_at
                    )
                    VALUES (%s, %s, 'rps_refund', %s, %s, %s)
                    """,
                    (
                        user_id,
                        bet,
                        opponent_id if user_id == challenger_id else challenger_id,
                        game_id,
                        now,
                    ),
                )

            status = "draw"
        else:
            winner_id = int(winner_id)

            if winner_id not in (challenger_id, opponent_id):
                raise ValueError("승자 정보가 올바르지 않습니다.")

            loser_id = (
                opponent_id if winner_id == challenger_id else challenger_id
            )

            conn.execute(
                """
                UPDATE users
                SET game_points = game_points + %s, updated_at = %s
                WHERE user_id = %s
                """,
                (pot, now, winner_id),
            )

            conn.execute(
                """
                INSERT INTO game_point_logs (
                    user_id, amount, reason,
                    related_user_id, game_id, created_at
                )
                VALUES (%s, %s, 'rps_win', %s, %s, %s)
                """,
                (winner_id, pot, loser_id, game_id, now),
            )

            conn.execute(
                """
                INSERT INTO rps_stats (
                    user_id, wins, games, net_points
                )
                VALUES (%s, 1, 1, %s)
                ON CONFLICT(user_id) DO UPDATE SET
                    wins = rps_stats.wins + 1,
                    games = rps_stats.games + 1,
                    net_points = rps_stats.net_points + excluded.net_points
                """,
                (winner_id, bet),
            )

            conn.execute(
                """
                INSERT INTO rps_stats (
                    user_id, losses, games, net_points
                )
                VALUES (%s, 1, 1, %s)
                ON CONFLICT(user_id) DO UPDATE SET
                    losses = rps_stats.losses + 1,
                    games = rps_stats.games + 1,
                    net_points = rps_stats.net_points + excluded.net_points
                """,
                (loser_id, -bet),
            )

            status = "finished"

        conn.execute(
            """
            UPDATE rps_games
            SET status = %s, winner_id = %s, updated_at = %s
            WHERE game_id = %s
            """,
            (status, winner_id, now, game_id),
        )

        return pot



def create_point_drop(
    drop_id,
    chat_id,
    creator_id,
    total_points,
    max_claims,
    message_id=None,
):
    total_points = int(total_points)
    max_claims = int(max_claims)

    if total_points < 1 or max_claims < 1:
        raise ValueError("총 포인트와 인원은 1 이상이어야 합니다.")

    if total_points % max_claims != 0:
        raise ValueError("총 포인트가 인원수로 나누어떨어져야 합니다.")

    points_per_claim = total_points // max_claims
    now = now_iso()

    with db() as conn:
        conn.execute(
            """
            INSERT INTO point_drops (
                drop_id, chat_id, message_id, creator_id,
                total_points, max_claims, points_per_claim,
                claimed_count, status, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, 0, 'active', %s, %s)
            """,
            (
                drop_id,
                int(chat_id),
                message_id,
                int(creator_id),
                total_points,
                max_claims,
                points_per_claim,
                now,
                now,
            ),
        )

    return points_per_claim


def set_point_drop_message_id(drop_id, message_id):
    with db() as conn:
        conn.execute(
            """
            UPDATE point_drops
            SET message_id = %s, updated_at = %s
            WHERE drop_id = %s
            """,
            (int(message_id), now_iso(), drop_id),
        )


def get_point_drop(drop_id):
    with db() as conn:
        return conn.execute(
            """
            SELECT *
            FROM point_drops
            WHERE drop_id = %s
            """,
            (drop_id,),
        ).fetchone()


def claim_point_drop(drop_id, user_id):
    user_id = int(user_id)

    with db() as conn:
        drop = conn.execute(
            """
            SELECT *
            FROM point_drops
            WHERE drop_id = %s
            FOR UPDATE
            """,
            (drop_id,),
        ).fetchone()

        if not drop:
            raise ValueError("존재하지 않는 포인트 뿌리기입니다.")

        if drop["status"] != "active":
            raise ValueError("이미 종료된 포인트 뿌리기입니다.")

        if int(drop["creator_id"]) == user_id:
            raise ValueError("뿌리기를 만든 사람은 받을 수 없습니다.")

        already = conn.execute(
            """
            SELECT 1
            FROM point_drop_claims
            WHERE drop_id = %s AND user_id = %s
            """,
            (drop_id, user_id),
        ).fetchone()

        if already:
            raise ValueError("이미 포인트를 받았습니다.")

        claimed_count = int(drop["claimed_count"])
        max_claims = int(drop["max_claims"])

        if claimed_count >= max_claims:
            conn.execute(
                """
                UPDATE point_drops
                SET status = 'finished', updated_at = %s
                WHERE drop_id = %s
                """,
                (now_iso(), drop_id),
            )
            raise ValueError("선착순 지급이 종료되었습니다.")

        user = conn.execute(
            """
            SELECT game_points
            FROM users
            WHERE user_id = %s
            FOR UPDATE
            """,
            (user_id,),
        ).fetchone()

        if not user:
            raise ValueError("먼저 채팅을 한 번 입력한 뒤 다시 눌러주세요.")

        points = int(drop["points_per_claim"])
        new_count = claimed_count + 1
        new_status = "finished" if new_count >= max_claims else "active"
        now = now_iso()

        conn.execute(
            """
            UPDATE users
            SET game_points = game_points + %s, updated_at = %s
            WHERE user_id = %s
            """,
            (points, now, user_id),
        )
        conn.execute(
            """
            INSERT INTO point_drop_claims (
                drop_id, user_id, points, created_at
            )
            VALUES (%s, %s, %s, %s)
            """,
            (drop_id, user_id, points, now),
        )
        conn.execute(
            """
            INSERT INTO game_point_logs (
                user_id, amount, reason, related_user_id, game_id, created_at
            )
            VALUES (%s, %s, 'point_drop', %s, %s, %s)
            """,
            (
                user_id,
                points,
                int(drop["creator_id"]),
                drop_id,
                now,
            ),
        )
        conn.execute(
            """
            UPDATE point_drops
            SET claimed_count = %s, status = %s, updated_at = %s
            WHERE drop_id = %s
            """,
            (new_count, new_status, now, drop_id),
        )

        balance = int(user["game_points"]) + points

        return {
            "points": points,
            "balance": balance,
            "claimed_count": new_count,
            "max_claims": max_claims,
            "status": new_status,
            "total_points": int(drop["total_points"]),
            "points_per_claim": points,
        }


def get_rps_stats(user_id):
    user_id = int(user_id)

    with db() as conn:
        row = conn.execute(
            "SELECT * FROM rps_stats WHERE user_id = %s",
            (user_id,),
        ).fetchone()

        if row:
            return row

        conn.execute(
            """
            INSERT INTO rps_stats (user_id)
            VALUES (%s)
            ON CONFLICT(user_id) DO NOTHING
            """,
            (user_id,),
        )

        return conn.execute(
            "SELECT * FROM rps_stats WHERE user_id = %s",
            (user_id,),
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
            LIMIT %s
            """,
            (int(limit),),
        ).fetchall()
