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

        CREATE TABLE IF NOT EXISTS riddles (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            answer   TEXT NOT NULL
        );
    """)

    # Seed riddles from the original text files only if the table is empty
    count = conn.execute("SELECT COUNT(*) FROM riddles").fetchone()[0]
    if count == 0:
        riddles_path = os.path.join(os.path.dirname(DB_PATH) or ".", "-riddles.txt")
        answers_path = os.path.join(os.path.dirname(DB_PATH) or ".", "-answers.txt")
        if os.path.exists(riddles_path) and os.path.exists(answers_path):
            with open(riddles_path, "r") as f:
                questions = f.read().splitlines()
            with open(answers_path, "r") as f:
                answers = f.read().splitlines()
            seed = list(zip(questions, answers))
        else:
            # Fallback seed data if text files are not available
            seed = [
                ("It is greater than God and more evil than the devil. The poor have it, the rich need it and if you eat it you'll die. What is it?", "Nothing"),
                ("What always runs but never walks, often murmurs, never talks, has a bed but never sleeps, has a mouth but never eats?", "River"),
                ("The more you have of it, the less you see. What is it?", "Darkness"),
                ("What English word has three consecutive double letters?", "Bookkeeper"),
                ("What's black when you get it, red when you use it, and white when you're all through with it?", "Charcoal"),
                ("All about, but cannot be seen, Can be captured, cannot be held, No throat, but can be heard.", "Wind"),
                ("Until I am measured I am not known, Yet how you miss me when I have flown.", "Time"),
                ("When set loose, I fly away, Never so cursed as when I go astray.", "Fart"),
                ("Lighter than what I am made of, More of me is hidden Than is seen.", "Iceberg"),
                ("Three lives have I. Gentle enough to soothe the skin, Light enough to caress the sky, Hard enough to crack rocks.", "Water"),
            ]
        conn.executemany("INSERT INTO riddles (question, answer) VALUES (?, ?)", seed)

    conn.commit()
    conn.close()


def get_or_create_user(github_id, username, email):
    db = get_db()
    admin_github_id = os.environ.get("ADMIN_GITHUB_ID", "")
    # SR4: Role assigned server-side based on verified GitHub ID — no client-supplied role claim trusted
    role = "admin" if str(github_id) == str(admin_github_id) and admin_github_id else "user"

    user = db.execute("SELECT * FROM users WHERE github_id = ?", (str(github_id),)).fetchone()
    if user is None:
        db.execute(
            "INSERT INTO users (github_id, username, email, role) VALUES (?, ?, ?, ?)",
            (str(github_id), username, email, role),
        )
    else:
        # Update username/email on each login; re-evaluate role if admin ID change, have to do it incase admin changes
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


# ---------------------------------------------------------------------------
# Riddle CRUD — SR13: All queries use parameterised statements
# ---------------------------------------------------------------------------


def get_all_riddles():
    """Return all riddles (for admin listing)."""
    db = get_db()
    return db.execute("SELECT * FROM riddles ORDER BY id").fetchall()


def get_riddle_by_id(riddle_id):
    """Return a single riddle row by ID."""
    db = get_db()
    return db.execute("SELECT * FROM riddles WHERE id = ?", (riddle_id,)).fetchone()


def get_random_riddles(n=10):
    """Return n random riddles using ORDER BY RANDOM() LIMIT n."""
    db = get_db()
    return db.execute("SELECT * FROM riddles ORDER BY RANDOM() LIMIT ?", (n,)).fetchall()


def add_riddle(question, answer):
    """Insert a new riddle. Used by admin add route."""
    db = get_db()
    db.execute("INSERT INTO riddles (question, answer) VALUES (?, ?)", (question, answer))
    db.commit()


def delete_riddle(riddle_id):
    """Delete a riddle by ID. Used by admin delete route."""
    db = get_db()
    db.execute("DELETE FROM riddles WHERE id = ?", (riddle_id,))
    db.commit()


def get_riddle_count():
    """Return total number of riddles in the table."""
    db = get_db()
    return db.execute("SELECT COUNT(*) FROM riddles").fetchone()[0]
