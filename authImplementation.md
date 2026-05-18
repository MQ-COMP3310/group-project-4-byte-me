# Authentication Feature Implementation

## Overview
Replaced the original username-form system (which had no real authentication) with GitHub OAuth 2.0 login. Users now authenticate via GitHub — no passwords stored. Game state moved from plain-text files to a signed session cookie and SQLite database.

---

## New Dependencies (`requirements.txt`)
| Package | Purpose |
|---------|---------|
| `Flask-Dance[github]` | GitHub OAuth 2.0 flow |
| `Flask-Login` | Session management after OAuth |
| `Flask-WTF` | CSRF protection on all POST forms |
| `python-dotenv` | Load secrets from `.env` |
| `blinker` | Required by Flask signals (used by flask-dance) |

---

## New Files

### `db.py`
SQLite helper module. All database access goes through here.
- `get_db()` — returns a per-request connection via Flask's `g` object
- `close_db()` — registered on `teardown_appcontext` to close connections cleanly
- `init_db()` — creates tables (safe to re-run)
- `get_or_create_user(github_id, username, email)` — upserts user on login; auto-promotes to admin if GitHub ID matches `ADMIN_GITHUB_ID` env var
- `get_highscores()` — returns all scores joined with usernames, sorted by score DESC
- `add_highscore(user_id, score)` — inserts a new score row
- `delete_highscore(entry_id)` / `update_highscore(entry_id, new_score)` — admin operations

### `init_db.py`
CLI script — run once before starting the app to create the database tables:
```bash
python init_db.py
```

### `templates/admin.html`
Admin-only page listing all highscore entries with inline edit (score) and delete controls.

---

## SQLite Schema

```sql
CREATE TABLE users (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    github_id TEXT UNIQUE NOT NULL,
    username  TEXT NOT NULL,
    email     TEXT,
    role      TEXT NOT NULL DEFAULT 'user',  -- 'user' | 'admin'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE highscores (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    score     INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## Route Changes

| Old | New | Access |
|-----|-----|--------|
| `GET/POST /` | `GET /` | Public |
| `GET/POST /<username>` | `GET/POST /welcome` | Login required |
| `GET/POST /<username>/game` | `GET/POST /game` | Login required |
| `GET/POST /<username>/gameover` | `GET/POST /gameover` | Login required |
| `GET/POST /<username>/congratulations` | `GET/POST /congratulations` | Login required |
| `GET /highscores` | `GET /highscores` | Public |
| *(new)* | `GET /login/github` | Public — initiates OAuth |
| *(new)* | `GET /login/github/authorized` | Public — OAuth callback (flask-dance) |
| *(new)* | `GET /logout` | Login required |
| *(new)* | `GET /admin` | Admin only |
| *(new)* | `POST /admin/highscores/<id>/edit` | Admin only |
| *(new)* | `POST /admin/highscores/<id>/delete` | Admin only |

---

## Game State Changes
Game state (riddle index, wrong guesses, score) is now stored in Flask's **signed session cookie** instead of per-user text files. This eliminates the path traversal vulnerability and race conditions from the original implementation.

| Old (files) | New (session cookie) |
|-------------|----------------------|
| `data/user-<name>-guesses.txt` | `session['guesses']` (list) |
| `data/user-<name>-score.txt` | `session['score']` (int) |
| Global `riddle_index` variable | `session['riddle_index']` (int) |


---

### First run
```bash
python3.11 -m venv env
source env/bin/activate
pip install -r requirements.txt
python init_db.py
python run.py
```

### Setting an admin
Set `ADMIN_GITHUB_ID` to your numeric GitHub ID in `.env`. On your next login the role is updated automatically. Find your ID at `https://api.github.com/users/<your_username>` (the `id` field).

---

## Security Controls Added
- Secret key from environment variable — not hardcoded (fixes CWE-798)
- Debug mode controlled via env var — not hardcoded to `True`
- CSRF tokens on all POST forms via Flask-WTF `CSRFProtect` (this is part of other sections but ive included it here for now anyways)
- OAuth state parameter handled automatically by flask-dance (prevents CSRF on auth flow)
- Username sourced from verified GitHub identity — eliminates IDOR via URL manipulation
- `riddle_index` validated against riddle list length before use — prevents IndexError
- Admin role assigned server-side from env var — no client-supplied role trusted
- Flask-Login session regenerated on login — prevents session fixation
- Only public GitHub profile data stored (GitHub ID, login, email) — minimal data principle

---

## Session Log — 2026-05-18

### Work Completed

#### Rate Limiting
- Added `Flask-Limiter` to `requirements.txt`
- Applied `@limiter.limit("5 per minute")` to the `/login/github` route to throttle brute-force attempts
- Registered `@app.errorhandler(429)` → `templates/errors/429.html` so rate-limit hits return a styled page instead of Flask's default

#### Error Handlers + Templates
- Registered `@app.errorhandler(400)` → `templates/errors/400.html` ("Bad Request")
- Registered `@app.errorhandler(403)` → `templates/errors/403.html` ("Forbidden")
- All error pages extend `base.html` and include a back-to-home button — consistent UX, no internal detail leaked

#### Security Requirement Comments (`SRX`)
- Added inline comments throughout `run.py` and `db.py` referencing named security requirements (e.g. `# SECURITY: SR3 — ...`) to satisfy the report's traceability requirement

#### Admin Score Bounds Fix
- `POST /admin/highscores/<id>/edit` now rejects scores outside `[0, 30]` with `abort(400)` — previously no server-side bounds check existed

#### Documentation
- Recorded access-control rules, endpoints, and DB schema in the shared Google Docs group project document

---

### Next Steps (Action Items)

| # | Item | Owner | Notes |
|---|------|-------|-------|
| 1 | Improve GET-route error handling | Alik | Many GET routes currently fall through to a 500 on unexpected state; replace with explicit `abort(404)` / redirects where appropriate |
| 2 | Group sync — task allocation | All | Identify who needs help; unblock remaining Part 2/3 tasks |
| 3 | Threat model update (Task 6) | Alik | Update STRIDE diagram + trust boundaries to reflect auth feature |
| 4 | Agree on 2 additional features (Part 3) | All | Needed before Task 8/9 implementation can start |
| 5 | Low-severity vulnerability triage | Alik | Address remaining findings from CLAUDE.md if time permits (e.g. silent score-parse errors, highscores IndexError) |
