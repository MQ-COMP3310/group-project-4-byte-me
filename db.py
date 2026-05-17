import sqlite3
import os

DB_PATH = os.environ.get("DB_PATH", "data/riddle_game.db")


def get_db():
    from flask import g
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(error=None):
    from flask import g
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            github_id   TEXT    UNIQUE NOT NULL,
            username    TEXT    NOT NULL,
            email       TEXT,
            role        TEXT    NOT NULL DEFAULT 'user',
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS highscores (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            score       INTEGER NOT NULL,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()


def get_or_create_user(github_id, username, email):
    db = get_db()
    admin_github_id = os.environ.get("ADMIN_GITHUB_ID", "")
    # SECURITY: Role assigned server-side based on verified GitHub ID — no client-supplied role claim trusted
    role = "admin" if str(github_id) == str(admin_github_id) and admin_github_id else "user"

    user = db.execute("SELECT * FROM users WHERE github_id = ?", (str(github_id),)).fetchone()
    if user is None:
        db.execute(
            "INSERT INTO users (github_id, username, email, role) VALUES (?, ?, ?, ?)",
            (str(github_id), username, email, role),
        )
    else:
        # Update username/email on each login; re-evaluate role if admin ID changes
        db.execute(
            "UPDATE users SET username = ?, email = ?, role = ? WHERE github_id = ?",
            (username, email, role, str(github_id)),
        )
    db.commit()
    return db.execute("SELECT * FROM users WHERE github_id = ?", (str(github_id),)).fetchone()


def get_highscores():
    db = get_db()
    return db.execute("""
        SELECT h.id, u.username, h.score, h.created_at
        FROM highscores h
        JOIN users u ON h.user_id = u.id
        ORDER BY h.score DESC
    """).fetchall()


def add_highscore(user_id, score):
    db = get_db()
    db.execute("INSERT INTO highscores (user_id, score) VALUES (?, ?)", (user_id, score))
    db.commit()


def delete_highscore(entry_id):
    db = get_db()
    db.execute("DELETE FROM highscores WHERE id = ?", (entry_id,))
    db.commit()


def update_highscore(entry_id, new_score):
    db = get_db()
    db.execute("UPDATE highscores SET score = ? WHERE id = ?", (new_score, entry_id))
    db.commit()
