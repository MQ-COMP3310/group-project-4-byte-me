import pytest


@pytest.fixture
def logged_in_client(client):
    """
    Logs in a fake user by directly setting Flask-Login session values.

    This avoids needing to run the GitHub OAuth flow during tests.
    """
    with client.session_transaction() as sess:
        sess["_user_id"] = "1"
        sess["_fresh"] = True
        sess["riddle_index"] = 0
        sess["guesses"] = []
        sess["score"] = 0
        sess["hint_used"] = False

    return client


def test_hint_button_sets_first_letter(logged_in_client):
    """
    Requesting a hint should store the first letter of the current answer
    in the session.
    """
    response = logged_in_client.post(
        "/game",
        data={
            "riddle_index": "0",
            "action": "hint",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    with logged_in_client.session_transaction() as sess:
        assert sess["hint_used"] is True
        assert "hint" in sess
        assert len(sess["hint"]) == 1


def test_hint_is_displayed_on_game_page(logged_in_client):
    """
    After requesting a hint, the game page should display the hint text.
    """
    logged_in_client.post(
        "/game",
        data={
            "riddle_index": "0",
            "action": "hint",
        },
        follow_redirects=True,
    )

    response = logged_in_client.get("/game")

    assert response.status_code == 200
    assert b"The answer starts with" in response.data


def test_hint_button_hidden_after_use(logged_in_client):
    """
    Once a hint has been used, the hint button should not be shown again
    for the same riddle.
    """
    logged_in_client.post(
        "/game",
        data={
            "riddle_index": "0",
            "action": "hint",
        },
        follow_redirects=True,
    )

    response = logged_in_client.get("/game")

    assert response.status_code == 200
    assert b'value="hint"' not in response.data


def test_hint_deducts_one_point_when_answer_correct(logged_in_client):
    """
    If the user asks for a hint and then answers correctly on the first try,
    the score for that riddle should be 2 instead of 3.
    """
    logged_in_client.post(
        "/game",
        data={
            "riddle_index": "0",
            "action": "hint",
        },
    )

    # This assumes the first answer in data/-answers.txt is being used.
    # Change "Bottle" to the real first answer if needed.
    response = logged_in_client.post(
        "/game",
        data={
            "riddle_index": "0",
            "action": "answer",
            "answer": "Bottle",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    with logged_in_client.session_transaction() as sess:
        assert sess["score"] == 2
        assert sess["hint_used"] is False
        assert "hint" not in sess


def test_invalid_riddle_index_is_rejected(logged_in_client):
    """
    A user should not be able to edit the hidden riddle_index field
    and request a hint for a non-existent riddle.
    """
    response = logged_in_client.post(
        "/game",
        data={
            "riddle_index": "999",
            "action": "hint",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400

    
def test_cannot_request_hint_for_future_riddle(logged_in_client):
    """
    The submitted riddle_index should match the current session riddle_index.
    This prevents users from modifying the hidden form field to get hints
    for riddles they have not reached yet.
    """
    response = logged_in_client.post(
        "/game",
        data={
            "riddle_index": "5",
            "action": "hint",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400