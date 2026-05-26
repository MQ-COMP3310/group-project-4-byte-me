# Feature One: Move Riddles to Database + Admin CRUD + Random Selection

## What Was Done

This feature migrates riddle storage from plaintext files (`data/-riddles.txt`, `data/-answers.txt`) into the SQLite database, adds admin routes for managing riddles (add/delete), and randomises riddle selection per game session. It also fixes the riddle-index manipulation bug where a player could skip riddles by posting a different `riddle_index` in the form.

## Why It Was Done

The original file-based approach had several security and reliability problems identified in the threat model:

- **Answers stored in plaintext on disk** (TB3 threat #15) — the `data/-answers.txt` file was trivially readable and accessible via the path traversal vulnerability.
- **Missing files crash the app** (threat #16) — no error handling around `open()` calls; a missing or deleted text file would cause an unhandled exception and HTTP 500.
- **File tampering** (threat #17) — anyone with filesystem access could modify riddle/answer files to cheat or disrupt the game.
- **Riddle-index manipulation** (STRIDE threat #4) — the app trusted the `riddle_index` value submitted in the POST form, allowing a player to skip directly to any riddle by changing the hidden field value.
- **No admin management** — there was no way to add or remove riddles without manually editing files on the server.
- **Fixed riddle order** — every game presented riddles in the same order, making the game trivially repeatable once answers were known.

## How It Was Implemented

### 1. Database Schema & Seed Data (`db.py`)

Added a `riddles` table to `init_db()`:

```sql
CREATE TABLE IF NOT EXISTS riddles (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    answer   TEXT NOT NULL
);
```

The table is seeded with the original 10 riddles on first run. Seeding reads from the text files if they exist, otherwise falls back to hardcoded data. Seeding only occurs when the table is empty (prevents duplicates on re-run).

Six new CRUD functions were added, all using parameterised queries (SR13):
- `get_all_riddles()` — returns all riddles for the admin listing page
- `get_riddle_by_id(riddle_id)` — returns a single riddle by primary key
- `get_random_riddles(n=10)` — selects n random riddles via `ORDER BY RANDOM() LIMIT n`
- `add_riddle(question, answer)` — inserts a new riddle (admin add route)
- `delete_riddle(riddle_id)` — removes a riddle by ID (admin delete route)
- `get_riddle_count()` — returns total riddle count (used for the minimum-10 deletion guard)

### 2. Game Logic Changes (`run.py`)

**Removed:** The `_riddles()` and `_answers()` helper functions that read from text files on every request.

**`/welcome` POST** — When a player starts a game, 10 random riddles are selected from the database. Their IDs are stored in `session["riddle_ids"]` alongside the existing `riddle_index`, `guesses`, and `score` session keys. If fewer than 10 riddles exist in the DB, the game cannot start (flash message + redirect).

**`/game` GET/POST** — Riddles are now fetched from the DB by ID using the session's `riddle_ids` list. The key security fix: the submitted `riddle_index` from the form is validated against `session["riddle_index"]` — if they don't match, the request is rejected with 400. This prevents riddle skipping (STRIDE threat #4). The template now receives `riddle_text` (a single string) instead of the full riddle list.

**`/gameover`** — Now also clears `session["riddle_ids"]` on game end.

### 3. Admin Riddle Management Routes (`run.py`)

Three new routes, all protected by `@login_required` and server-side `is_admin` checks (SR4):

- **`GET /admin/riddles`** — Lists all riddles in a table with delete buttons and an add form.
- **`POST /admin/riddles/add`** — Adds a new riddle. Validates that both question and answer are non-empty (400 otherwise).
- **`POST /admin/riddles/<id>/delete`** — Deletes a riddle by ID. Blocked if only 10 riddles remain (minimum required for a game session), with a flash message explaining why.

### 4. Template Changes

- **`templates/game.html`** — Changed `{{ riddles[riddle_index] }}` to `{{ riddle_text }}` (single line change, line 11).
- **`templates/admin_riddles.html`** — New template following the existing `admin.html` pattern. Contains a table listing all riddles with inline delete forms, and an add-riddle form at the bottom. All forms include CSRF tokens (SR11).
- **`templates/admin.html`** — Added a "Manage Riddles" link/button pointing to the new riddle management page.

### 5. Test Changes

**`tests/conftest.py`:**
- Updated `clean_db` fixture to also delete riddle rows and re-seed via `database.init_db()` after each test.
- Added `start_game_session(client)` helper that sets up a valid game session with 10 riddle IDs from the DB (used by tests that previously set session state manually).

**`tests/tests.py`:**
- Updated `test_csrf_game_post_blocked` to use `start_game_session()` instead of manually setting session keys (missing `riddle_ids` would break it).
- Updated `test_fuzz_game_answer` to use `start_game_session()`.
- Updated `test_fuzz_riddle_index` — since the session index is now authoritative, only `riddle_index == 0` (matching the session) is valid; all other values return 400.
- Added 9 new tests in `TestAdminRiddles`:

  - **`test_unauthenticated_admin_riddles`** — No user is logged in. GETs `/admin/riddles`. Expects 302 redirect and verifies the `Location` header points to the login page (`/`), not the admin page. Confirms `@login_required` is active on the route.

  - **`test_regular_user_admin_riddles`** — Logs in as a `role='user'` (non-admin) account. GETs `/admin/riddles`. Expects 403 Forbidden. Proves that authentication alone is not enough — the server-side `is_admin` check (SR4) blocks access for non-admin roles.

  - **`test_admin_view_riddles`** — Logs in as a `role='admin'` account. GETs `/admin/riddles`. Expects 200 OK and verifies the response HTML contains actual riddle data from the seeded DB (e.g. "Nothing" or "River"), confirming the template renders with real content, not just an empty page.

  - **`test_admin_add_riddle`** — Logs in as admin. Records the riddle count before the action. POSTs a valid question and answer to `/admin/riddles/add`. Expects 302 redirect. Then queries the DB directly to verify the new riddle exists and the total count increased by exactly 1.

  - **`test_admin_add_riddle_empty_fields`** — Logs in as admin. POSTs with (a) empty question and (b) empty answer. Expects 400 Bad Request in both cases. Then queries the DB to verify the riddle count is unchanged — confirming server-side validation rejected the requests and nothing was inserted.

  - **`test_admin_delete_riddle`** — Logs in as admin. Inserts an 11th riddle directly into the DB (so the minimum-10 guard won't block). Asserts the pre-count is 11. POSTs to delete that riddle. Expects 302 redirect. Then verifies the riddle no longer exists in the DB and the count decreased by exactly 1.

  - **`test_admin_delete_riddle_minimum_guard`** — Logs in as admin. Asserts the DB has exactly 10 seeded riddles. Attempts to delete one. Expects 302 redirect (with flash message). Then verifies the riddle still exists and the count remains exactly 10 — confirming the guard prevented deletion to ensure games can always select 10 riddles.

  - **`test_non_admin_add_riddle`** — Logs in as a regular (non-admin) user. Records the riddle count. POSTs valid riddle data to `/admin/riddles/add`. Expects 403 Forbidden. Then verifies the DB count is unchanged — confirming the 403 truly blocked the insert, not just the response code.

  - **`test_non_admin_delete_riddle`** — Logs in as a regular user. Inserts an 11th riddle first (so the minimum-10 guard is not a factor — we want to test role enforcement specifically). POSTs to delete that riddle. Expects 403 Forbidden. Then verifies the riddle still exists in the DB — confirming role enforcement blocked the operation.

### 6. Files Changed

| File | Change Type |
|------|-------------|
| `db.py` | Modified — added `riddles` table schema, seed logic, 6 CRUD functions |
| `run.py` | Modified — removed file helpers, updated `/welcome` and `/game`, added 3 admin routes |
| `templates/game.html` | Modified — one line: `riddles[riddle_index]` to `riddle_text` |
| `templates/admin_riddles.html` | **New** — admin riddle management page |
| `templates/admin.html` | Modified — added link to riddle management |
| `tests/conftest.py` | Modified — updated `clean_db`, added `start_game_session` helper |
| `tests/tests.py` | Modified — updated 3 existing tests, added 9 new admin riddle tests |

### 7. Threat Model Items Addressed

| Threat | Status |
|--------|--------|
| #4 — riddle_index manipulation (skip riddles) | Fixed — session index is authoritative |
| #15 — answers in plaintext file on disk | Fixed — answers now in DB, no separate plaintext file |
| #16 — missing file crashes the app | Fixed — file reads eliminated entirely |
| #17 — file tampering | Fixed — no more data files to tamper with |

### 8. Verification

All 31 tests pass (22 existing + 9 new):

```
tests/tests.py  31 passed
```
