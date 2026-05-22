"""conftest.py — pytest fixtures for the Flask riddle-game test suite.

Sets up a test Flask application with:
- Temporary file-based SQLite database (never touches the production DB)
- CSRF disabled by default (enabled selectively for CSRF tests)
- A /test-login/<user_id> route registered at import time to bypass GitHub OAuth
- DB cleanup and rate-limiter reset between every test
"""

import os
import sys
import sqlite3
import tempfile

# ---------------------------------------------------------------------------
# Path + working directory setup (must come first)
# ---------------------------------------------------------------------------

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# Change CWD so run.py's relative file-paths (data/-riddles.txt etc.) resolve.
os.chdir(PROJECT_ROOT)

# ---------------------------------------------------------------------------
# Environment variables (must be set before importing run.py)
# ---------------------------------------------------------------------------

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")
os.environ.setdefault("GITHUB_OAUTH_CLIENT_ID", "test-client-id")
os.environ.setdefault("GITHUB_OAUTH_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("ADMIN_GITHUB_ID", "admin_github_sentinel_999")
# Allow OAuth over plain HTTP in tests (flask-dance requirement)
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

# Create a fresh temporary SQLite file for the entire test session.
# tempfile.mkstemp always creates a new file, so production data is never touched.
_db_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.environ["DB_PATH"] = _db_path

# ---------------------------------------------------------------------------
# Import application (after env vars are fully set)
# ---------------------------------------------------------------------------

import pytest
import run
import db as database
from run import app as flask_app, limiter

# ---------------------------------------------------------------------------
# Configure the app for testing (done once at import time)
# ---------------------------------------------------------------------------

flask_app.config.update(
    {
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-secret-key-for-testing-only",
    }
)

# Initialise the schema in the temp DB.
database.init_db()

# ---------------------------------------------------------------------------
# Test-only login route — bypasses GitHub OAuth
# Registered here (not inside a fixture) so it is added exactly once.
# ---------------------------------------------------------------------------


@flask_app.route("/test-login/<int:user_id>")
def _test_login(user_id):
    """Log in any DB user by ID without going through GitHub OAuth."""
    from flask_login import login_user

    row = database.get_db().execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if row is None:
        return "User not found", 404
    user = run.User(
        row["id"], row["github_id"], row["username"], row["email"], row["role"]
    )
    login_user(user)
    return "OK", 200


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def app():
    """Session-scoped app fixture — returns the already-configured Flask app."""
    yield flask_app
    # Teardown: close and delete the temporary DB file.
    os.close(_db_fd)
    os.unlink(_db_path)


@pytest.fixture
def client(app):
    """Test client with CSRF disabled (the default for most tests)."""
    return app.test_client()


@pytest.fixture
def csrf_client(app):
    """Test client with CSRF enforcement turned ON."""
    app.config["WTF_CSRF_ENABLED"] = True
    yield app.test_client()
    app.config["WTF_CSRF_ENABLED"] = False


@pytest.fixture(autouse=True)
def clean_db():
    """Delete all rows after every test so each test starts with a clean DB."""
    yield
    conn = sqlite3.connect(_db_path)
    conn.execute("DELETE FROM highscores")
    conn.execute("DELETE FROM users")
    conn.execute("DELETE FROM riddles")
    conn.commit()
    conn.close()
    # Re-seed riddles so next test has clean data
    database.init_db()


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Clear the in-memory rate-limiter storage after every test."""
    yield
    try:
        limiter._storage.reset()
    except Exception:
        pass


@pytest.fixture
def regular_user():
    """Insert a role='user' account into the DB and return its integer ID."""
    conn = sqlite3.connect(_db_path)
    conn.execute(
        "INSERT INTO users (github_id, username, email, role) VALUES (?, ?, ?, ?)",
        ("gh_regular_001", "regularuser", "regular@test.com", "user"),
    )
    conn.commit()
    user_id = conn.execute(
        "SELECT id FROM users WHERE github_id = ?", ("gh_regular_001",)
    ).fetchone()[0]
    conn.close()
    return user_id


@pytest.fixture
def admin_user():
    """Insert a role='admin' account into the DB and return its integer ID."""
    conn = sqlite3.connect(_db_path)
    conn.execute(
        "INSERT INTO users (github_id, username, email, role) VALUES (?, ?, ?, ?)",
        ("gh_admin_001", "adminuser", "admin@test.com", "admin"),
    )
    conn.commit()
    user_id = conn.execute(
        "SELECT id FROM users WHERE github_id = ?", ("gh_admin_001",)
    ).fetchone()[0]
    conn.close()
    return user_id


# ---------------------------------------------------------------------------
# Helper (imported by tests.py)
# ---------------------------------------------------------------------------


def login(client, user_id):
    """Call the test-only login route to establish a session for user_id."""
    resp = client.get(f"/test-login/{user_id}")
    assert resp.status_code == 200, f"Test login failed for user_id={user_id}"
    return client


def start_game_session(client):
    """Set up a valid game session with 10 riddle IDs from the DB."""
    with flask_app.app_context():
        riddle_ids = [r["id"] for r in database.get_random_riddles(10)]
    with client.session_transaction() as sess:
        sess["riddle_ids"] = riddle_ids
        sess["riddle_index"] = 0
        sess["guesses"] = []
        sess["score"] = 0
    return riddle_ids
