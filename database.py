import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from config import DB_PATH

_lock = threading.RLock()

def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def _connect():
    path = Path(DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    with _lock, _connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            display_name TEXT NOT NULL,
            tickets INTEGER NOT NULL DEFAULT 0 CHECK(tickets >= 0),
            total_points INTEGER NOT NULL DEFAULT 0,
            total_draws INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_users_username ON users(username COLLATE NOCASE);
        CREATE TABLE IF NOT EXISTS draws(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            reward_name TEXT NOT NULL,
            points INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ticket_logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            target_user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            amount INTEGER NOT NULL,
            before_tickets INTEGER NOT NULL,
            after_tickets INTEGER NOT NULL,
            chat_id INTEGER,
            created_at TEXT NOT NULL
        );
        """)

def upsert_user(user_id, username, display_name):
    username = username.lower() if username else None
    now = _now()
    with _lock, _connect() as conn:
        conn.execute("""
        INSERT INTO users(user_id,username,display_name,tickets,total_points,total_draws,created_at,updated_at)
        VALUES(?,?,?,0,0,0,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
          username=excluded.username,
          display_name=excluded.display_name,
          updated_at=excluded.updated_at
        """, (user_id, username, display_name, now, now))

def get_user(user_id):
    with _lock, _connect() as conn:
        return conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

def find_user_by_username(username):
    with _lock, _connect() as conn:
        return conn.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (username.lstrip('@').lower(),)).fetchone()

def change_tickets(admin_id, target_user_id, delta, chat_id):
    with _lock, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT tickets FROM users WHERE user_id=?", (target_user_id,)).fetchone()
        if not row:
            raise LookupError("등록되지 않은 회원입니다.")
        before = int(row["tickets"])
        after = before + delta
        if after < 0:
            raise ValueError(f"보유 뽑기권이 {before}장이라 {abs(delta)}장을 회수할 수 없습니다.")
        conn.execute("UPDATE users SET tickets=?,updated_at=? WHERE user_id=?", (after,_now(),target_user_id))
        conn.execute("""
        INSERT INTO ticket_logs(admin_id,target_user_id,action,amount,before_tickets,after_tickets,chat_id,created_at)
        VALUES(?,?,?,?,?,?,?,?)
        """, (admin_id,target_user_id,"grant" if delta>0 else "revoke",abs(delta),before,after,chat_id,_now()))
        conn.commit()
        return before, after

def perform_draws(user_id, rewards, count):
    with _lock, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT tickets FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            raise LookupError("등록되지 않은 회원입니다.")
        tickets = int(row["tickets"])
        if tickets < count:
            raise ValueError(f"뽑기권이 부족합니다. 현재 {tickets}장 보유 중입니다.")
        total = sum(p for _, p in rewards)
        conn.execute("""
        UPDATE users SET tickets=tickets-?, total_points=total_points+?, total_draws=total_draws+?, updated_at=?
        WHERE user_id=?
        """, (count,total,count,_now(),user_id))
        conn.executemany("INSERT INTO draws(user_id,reward_name,points,created_at) VALUES(?,?,?,?)",
                         [(user_id,n,p,_now()) for n,p in rewards])
        conn.commit()
        return tickets-count, total

def get_ranking(limit=10):
    with _lock, _connect() as conn:
        return conn.execute("""
        SELECT * FROM users WHERE total_draws>0
        ORDER BY total_points DESC,total_draws DESC,user_id ASC LIMIT ?
        """, (limit,)).fetchall()

def get_draw_history(user_id, limit=10):
    with _lock, _connect() as conn:
        return conn.execute("SELECT * FROM draws WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id,limit)).fetchall()

