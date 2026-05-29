import pytest

import db as database


def login_as_user(client, role="user"):
    """
    Creates a test user and logs them in by setting Flask-Login session values.
    This avoids needing to run the GitHub OAuth flow during tests.
    """
    with client.application.app_context():
        db = database.get_db()
        db.execute(
            """
            INSERT INTO users (id, github_id, username, email, role)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET role = excluded.role
            """,
            (1, "test-github-id", "testuser", "test@example.com", role),
        )
        db.commit()

    with client.session_transaction() as sess:
        sess["_user_id"] = "1"
        sess["_fresh"] = True


def start_game_session(client):
    """
    Starts a valid game session using 10 riddle IDs from the database.
    """
    with client.application.app_context():
        riddles = database.get_random_riddles(10)
        riddle_ids = [r["id"] for r in riddles]

    with client.session_transaction() as sess:
        sess["riddle_ids"] = riddle_ids
        sess["riddle_index"] = 0
        sess["guesses"] = []
        sess["score"] = 0
        sess["hint_used"] = False
        sess.pop("hint", None)

    return riddle_ids


def get_current_answer(client):
    """
    Gets the answer for the current riddle in the test session.
    """
    with client.session_transaction() as sess:
        riddle_ids = sess["riddle_ids"]
        riddle_index = sess["riddle_index"]
        current_riddle_id = riddle_ids[riddle_index]

    with client.application.app_context():
        riddle = database.get_riddle_by_id(current_riddle_id)
        return riddle["answer"]


def test_hint_uses_database_answer_first_letter(client):
    """
    Security/integrity: the hint must be generated server-side from the
    database riddle answer, not supplied by the client.
    """
    login_as_user(client)
    start_game_session(client)

    answer = get_current_answer(client)
    expected_hint = answer.strip()[0]

    response = client.post(
        "/game",
        data={
            "riddle_index": "0",
            "action": "hint",
            "answer": "malicious-client-answer",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    with client.session_transaction() as sess:
        assert sess["hint"] == expected_hint
        assert sess["hint_used"] is True
        assert sess["hint"] != "m"


def test_hint_does_not_expose_full_answer(client):
    """
    Security: the page should reveal only the first character, not the full
    database answer.
    """
    login_as_user(client)
    start_game_session(client)

    answer = get_current_answer(client)

    client.post(
        "/game",
        data={
            "riddle_index": "0",
            "action": "hint",
        },
        follow_redirects=False,
    )

    response = client.get("/game")

    assert response.status_code == 200
    assert b"The answer starts with" in response.data
    assert answer.encode() not in response.data


def test_hint_rejects_tampered_riddle_index(client):
    """
    Security: a player must not be able to modify the hidden riddle_index
    field to request a hint for a different riddle.
    """
    login_as_user(client)
    start_game_session(client)

    response = client.post(
        "/game",
        data={
            "riddle_index": "5",
            "action": "hint",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400

    with client.session_transaction() as sess:
        assert "hint" not in sess
        assert sess["hint_used"] is False


def test_hint_rejects_non_integer_riddle_index(client):
    """
    Security: malformed riddle_index values should be rejected before they are
    used in database/session logic.
    """
    login_as_user(client)
    start_game_session(client)

    response = client.post(
        "/game",
        data={
            "riddle_index": "not-a-number",
            "action": "hint",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400


def test_hint_state_resets_after_correct_answer(client):
    """
    Integrity: hint state should not leak into the next riddle.
    """
    login_as_user(client)
    start_game_session(client)

    answer = get_current_answer(client)

    client.post(
        "/game",
        data={
            "riddle_index": "0",
            "action": "hint",
        },
        follow_redirects=False,
    )

    response = client.post(
        "/game",
        data={
            "riddle_index": "0",
            "action": "answer",
            "answer": answer,
        },
        follow_redirects=False,
    )

    assert response.status_code == 200

    with client.session_transaction() as sess:
        assert sess["riddle_index"] == 1
        assert sess["hint_used"] is False
        assert "hint" not in sess


def test_hint_penalty_applied_server_side(client):
    """
    Integrity: if a hint is used, the score should be reduced server-side.
    """
    login_as_user(client)
    start_game_session(client)

    answer = get_current_answer(client)

    client.post(
        "/game",
        data={
            "riddle_index": "0",
            "action": "hint",
        },
        follow_redirects=False,
    )

    client.post(
        "/game",
        data={
            "riddle_index": "0",
            "action": "answer",
            "answer": answer,
        },
        follow_redirects=False,
    )

    with client.session_transaction() as sess:
        assert sess["score"] == 2


def test_hint_requires_login(client):
    """
    Security: unauthenticated users should not be able to request hints.
    """
    response = client.post(
        "/game",
        data={
            "riddle_index": "0",
            "action": "hint",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert "/" in response.headers["Location"]