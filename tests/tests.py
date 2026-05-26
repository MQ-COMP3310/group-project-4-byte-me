"""tests.py — HTTP endpoint test suite for the Flask riddle-game.

Test categories
---------------
1. TestScoreRange      — score stored in DB is always in [0, 30]
2. TestAdminAccess     — non-admin / unauthenticated users cannot reach admin routes
3. TestCSRF            — every state-changing POST is blocked without a valid token
4. TestRateLimiting    — 10 req/min cap on /login/github; other routes unrestricted
5. TestFuzzing         — hypothesis property tests: status code + score invariants
6. TestAdditional      — session tampering, HTTP method enforcement, SQL injection
7. TestAdminRiddles    — CRUD and access control for admin riddle management
"""

import os
import re
import sqlite3
import sys

import pytest
from hypothesis import given, settings, strategies as st

# ---------------------------------------------------------------------------
# Path setup (mirrors conftest.py — safe to call twice)
# ---------------------------------------------------------------------------

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from conftest import _db_path, login, flask_app, start_game_session  # noqa: E402
import db as database

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Correct answers (index matches riddle position in data/-answers.txt)
ANSWERS = [
    "Nothing",
    "River",
    "Darkness",
    "Bookkeeper",
    "Charcoal",
    "Wind",
    "Time",
    "Fart",
    "Iceberg",
    "Water",
]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _start_game(client):
    """POST /welcome to initialise session game state."""
    return client.post("/welcome")


def _post_answer(client, riddle_index, answer):
    """POST an answer to /game (does not follow redirects)."""
    return client.post(
        "/game",
        data={"riddle_index": str(riddle_index), "answer": answer},
        follow_redirects=False,
    )


def _insert_highscore(user_id, score=15):
    """Insert a highscore row directly into the DB; return its entry_id."""
    conn = sqlite3.connect(_db_path)
    conn.execute(
        "INSERT INTO highscores (user_id, score) VALUES (?, ?)", (user_id, score)
    )
    conn.commit()
    entry_id = conn.execute(
        "SELECT id FROM highscores WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()[0]
    conn.close()
    return entry_id


def _insert_user(github_id, username, role="user"):
    """Insert a user directly into the DB; return their ID."""
    conn = sqlite3.connect(_db_path)
    conn.execute(
        "INSERT OR IGNORE INTO users (github_id, username, email, role) "
        "VALUES (?, ?, ?, ?)",
        (github_id, username, f"{username}@test.com", role),
    )
    conn.commit()
    user_id = conn.execute(
        "SELECT id FROM users WHERE github_id = ?", (github_id,)
    ).fetchone()[0]
    conn.close()
    return user_id


# =============================================================================
# 1. Score Range Tests
# =============================================================================


class TestScoreRange:
    """Score values stored in and accepted by the DB must always be in [0, 30]."""

    @pytest.mark.parametrize("score", ["abc", "", "None", "3.5"])
    def test_admin_edit_non_integer(self, client, admin_user, score):
        """Non-integer score values must be rejected; hypothesis uses st.integers() so
        strings are not covered by the property test below."""
        entry_id = _insert_highscore(admin_user)
        login(client, admin_user)
        resp = client.post(
            f"/admin/highscores/{entry_id}/edit", data={"score": score}
        )
        assert resp.status_code == 400  # Expected: 400 Bad Request (not a valid integer)

    # Valid scores [0, 30]         → Expected: 302 redirect (accepted)
    # Invalid scores outside range → Expected: 400 Bad Request (rejected)
    @settings(max_examples=50)
    @given(score=st.integers())
    def test_hypothesis_score_validation(self, score):
        """Property: only integers in [0, 30] are accepted; all others yield 400."""
        with flask_app.test_client() as c:
            user_id = _insert_user("hyp_admin_sc", "hypadmin", role="admin")
            entry_id = _insert_highscore(user_id)
            login(c, user_id)
            resp = c.post(
                f"/admin/highscores/{entry_id}/edit", data={"score": str(score)}
            )
            if 0 <= score <= 30:
                assert resp.status_code == 302, (  # Expected: 302 redirect (accepted)
                    f"Valid score {score} should be accepted (302), got {resp.status_code}"
                )
            else:
                assert resp.status_code == 400, (  # Expected: 400 Bad Request (rejected)
                    f"Invalid score {score} should be rejected (400), got {resp.status_code}"
                )


# =============================================================================
# 2. Admin Access Control Tests
# =============================================================================


class TestAdminAccess:
    """Unauthenticated and non-admin users must not reach any admin route."""

    @pytest.mark.parametrize(
        "method, path",
        [
            ("GET", "/admin"),
            ("GET", "/admin/riddles"),
            ("POST", "/admin/highscores/1/delete"),
            ("POST", "/admin/highscores/1/edit"),
            ("POST", "/admin/riddles/add"),
            ("POST", "/admin/riddles/1/delete"),
        ],
    )
    def test_unauthenticated_admin_routes_redirect_to_login(self, client, method, path):
        """No session cookie → every admin route must redirect to the login page."""
        resp = client.open(path, method=method)
        assert resp.status_code == 302, (
            f"Expected 302 redirect for unauthenticated {method} {path}, got {resp.status_code}"
        )
        location = resp.headers.get("Location", "")
        assert location.split("?")[0] == "/", (
            f"Expected redirect to login page (/), but {method} {path} redirected to: {location}"
        )

    @pytest.mark.parametrize(
        "method, path",
        [
            ("GET", "/admin"),
            ("GET", "/admin/riddles"),
            ("POST", "/admin/highscores/1/delete"),
            ("POST", "/admin/highscores/1/edit"),
            ("POST", "/admin/riddles/add"),
            ("POST", "/admin/riddles/1/delete"),
        ],
    )
    def test_non_admin_user_blocked_from_admin_routes(self, client, regular_user, method, path):
        """Authenticated non-admin user must get 403 on every admin route."""
        login(client, regular_user)
        resp = client.open(path, method=method)
        assert resp.status_code == 403, (
            f"Expected 403 Forbidden for non-admin {method} {path}, got {resp.status_code}"
        )

    def test_admin_user_dashboard(self, client, admin_user):
        """Admin-role user must be granted access to the admin panel."""
        login(client, admin_user)
        resp = client.get("/admin")
        assert resp.status_code == 200  # Expected: 200 OK (happy path)

    @pytest.mark.parametrize(
        "endpoint", ["/welcome", "/game", "/gameover", "/congratulations"]
    )
    def test_unauthenticated_game_routes_redirect(self, client, endpoint):
        """All game routes must redirect unauthenticated visitors to login."""
        resp = client.get(endpoint)
        assert resp.status_code == 302  # Expected: 302 redirect to login


# =============================================================================
# 3. CSRF Protection Tests
# =============================================================================


class TestCSRF:
    """POST requests without a valid CSRF token must be blocked with 400."""

    def test_csrf_game_post_blocked(self, csrf_client, regular_user):
        """POST to /game without a CSRF token must be rejected."""
        login(csrf_client, regular_user)
        start_game_session(csrf_client)
        resp = csrf_client.post(
            "/game", data={"riddle_index": "0", "answer": "Nothing"}
        )
        assert resp.status_code == 400  # Expected: 400 Bad Request (missing CSRF token)

    def test_csrf_admin_edit_blocked(self, csrf_client, admin_user):
        """POST to admin edit endpoint without a CSRF token must be rejected."""
        login(csrf_client, admin_user)
        resp = csrf_client.post("/admin/highscores/1/edit", data={"score": "5"})
        assert resp.status_code == 400  # Expected: 400 Bad Request (missing CSRF token)

    def test_valid_csrf_token_accepted(self, csrf_client, regular_user):                                                                                 
        """Extracting the real CSRF token from the form and submitting it must succeed."""                                                               
        login(csrf_client, regular_user)                                                                                                                 
        resp = csrf_client.get("/welcome")                                                                                                               
        assert resp.status_code == 200                                                                                                                   

        # Extract token from the hidden input (handles either attribute order)
        html = resp.data.decode()
        match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
        if not match:
            match = re.search(r'value="([^"]+)"[^>]*name="csrf_token"', html)
        assert match, "CSRF token not found in welcome form HTML"

        token = match.group(1)
        resp = csrf_client.post("/welcome", data={"csrf_token": token})
        assert resp.status_code != 400  # Expected: not 400 (valid token accepted)


# =============================================================================
# 5. Rate Limiting Tests
# =============================================================================


class TestRateLimiting:
    """
    The /login/github endpoint is rate-limited to 10 requests per minute per IP.
    """
    def test_login_rate_limit(self, client):
        """mirrors appendix A2 bash script tested before"""
        codes = [
            client.get("/login/github").status_code
            for _ in range(12)
        ]
        assert all(c == 302 for c in codes[:10]) #anything before should be 302 redirect to GitHub OAuth
        assert all(c == 429 for c in codes[10:]) #anything after 10 per minute should return 429


# =============================================================================
# 6. Hypothesis Fuzzing Tests
# =============================================================================


class TestFuzzing:
    """
    Property-based tests using Hypothesis.

    Invariants:
      A) Game answers yield 200 (wrong) or 302 (correct/redirect) for any input.
      B) Riddle index must match session state — mismatches are rejected with 400.
      C) Admin score edits: integers in [0, 30] → 302; everything else → 400.
    """

    # Any answer string, including unicode/special chars → Expected: not 500
    @settings(max_examples=50)
    @given(answer=st.text(max_size=500))
    def test_fuzz_game_answer(self, answer):
        """Any answer string must yield 200 (wrong) or 302 (correct/redirect)."""
        with flask_app.test_client() as c:
            uid = _insert_user("fuzz_ans", "fuzz_ans_user")
            login(c, uid)
            start_game_session(c)
            resp = c.post("/game", data={"riddle_index": "0", "answer": answer})
            assert resp.status_code in (200, 302), (
                f"Unexpected status {resp.status_code} on answer={answer!r}"
            )

    # Any integer riddle_index, including out-of-range → Expected: not 500
    @settings(max_examples=50)
    @given(riddle_index=st.integers())
    def test_fuzz_riddle_index(self, riddle_index):
        """Session index is authoritative: only riddle_index matching session (0) yields
        200/302; all others are rejected with 400."""
        with flask_app.test_client() as c:
            uid = _insert_user("fuzz_idx", "fuzz_idx_user")
            login(c, uid)
            start_game_session(c)
            resp = c.post(
                "/game",
                data={"riddle_index": str(riddle_index), "answer": "anything"},
            )
            if riddle_index == 0:
                # Matches session riddle_index=0, so accepted (200 wrong answer or 302 correct)
                assert resp.status_code in (200, 302)
            else:
                # Doesn't match session — rejected
                assert resp.status_code == 400

    # Any score value (integer or text) → Expected: not 500
    @settings(max_examples=50)
    @given(score=st.one_of(st.integers(), st.text(max_size=50)))
    def test_fuzz_admin_edit_score(self, score):
        """Admin edit score: valid integers in [0,30] yield 302; all others yield 400."""
        with flask_app.test_client() as c:
            uid = _insert_user("fuzz_sc_admin", "fuzz_sc_admin", role="admin")
            entry_id = _insert_highscore(uid)
            login(c, uid)
            resp = c.post(
                f"/admin/highscores/{entry_id}/edit", data={"score": str(score)}
            )
            try:                                                                                                                                               
                val = int(str(score))
                if 0 <= val <= 30:
                    assert resp.status_code == 302
                else:
                    assert resp.status_code == 400
            except ValueError:
                assert resp.status_code == 400


# =============================================================================
# 7. Additional Security Tests / do i want to keep these tests? they seem excessive for this assignment!
# =============================================================================


class TestAdditional:
    """Extra tests covering session integrity, method enforcement, and injection."""

    def test_session_invalidated_after_logout(self, client, regular_user):
        """After logout, the session is cleared and protected routes redirect to login.

        Flask signs session cookies with HMAC (SECRET_KEY).  Logout clears the
        server-side session, so even if an attacker replays an old cookie the
        user ID stored in it will no longer match an active session.
        """
        login(client, regular_user)
        assert client.get("/welcome").status_code == 200
        client.post("/logout")
        resp = client.get("/welcome")
        assert resp.status_code == 302, (  # Expected: 302 redirect to login (session gone)
            "After logout, /welcome must redirect to login"
        )

    def test_http_method_enforcement_logout(self, csrf_client, regular_user):
        """Logout must be POST-only (prevents logout CSRF via GET).

        Negative: GET → 405, POST without token → 400.
        Positive: POST with valid CSRF token → 302 redirect (session cleared).
        """
        login(csrf_client, regular_user)
        assert csrf_client.get("/logout").status_code == 405   # GET blocked
        assert csrf_client.post("/logout").status_code == 400  # POST without token blocked

        # Positive: a legitimate POST with a valid CSRF token must succeed.
        # Fetch a rendered page so Flask-WTF generates and signs the token,
        # then extract the signed value from the HTML (the raw session secret
        # alone is not accepted — Flask-WTF validates the signed form).
        page = csrf_client.get("/welcome")
        token = re.search(rb'name="csrf_token"\s+value="([^"]+)"', page.data).group(1).decode()
        resp = csrf_client.post("/logout", data={"csrf_token": token}, follow_redirects=False)
        assert resp.status_code == 302, "POST with valid CSRF token must redirect (logout success)"

    def test_sql_injection_admin_edit_score(self, client, admin_user):
        """SQL injection in the score field must not crash the app or drop tables.

        Parameterised queries in db.py prevent the injection; the non-integer
        string is caught by the int() conversion and results in 400.
        """
        entry_id = _insert_highscore(admin_user)
        login(client, admin_user)
        resp = client.post(
            f"/admin/highscores/{entry_id}/edit",
            data={"score": "1; DROP TABLE highscores"},
        )
        assert resp.status_code == 400  # Expected: 400 Bad Request (injection string rejected)

        # Confirm the table still exists (injection did not succeed)
        conn = sqlite3.connect(_db_path)
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='highscores'"
        ).fetchone()
        conn.close()
        assert table is not None, "highscores table was dropped — SQL injection succeeded"


# I didnt really under the test below so i've left here commented. we can add it later if we need it or remove it before submission

# =============================================================================
# 4. XSS Protection Tests
# =============================================================================

# Only payloads that contain HTML special characters — Jinja2 auto-escape prevents
# these from reaching the browser as raw markup.
# Note: "javascript:alert(1)" contains no HTML special chars, so Jinja2 does not
# escape it (it is safe in text content). It is only dangerous in href/src attributes,
# which is tested separately via the URL-scheme check below.
# XSS_PAYLOADS = [
#     "<script>alert(1)</script>",
#     '"><img src=x onerror=alert(1)>',
# ]


# # class TestXSS:
# #     """Jinja2 auto-escaping must prevent raw XSS payloads reaching the browser."""

# #     @pytest.mark.parametrize("payload", XSS_PAYLOADS)
# #     def test_xss_highscores_page(self, client, payload):
# #         """Usernames with HTML special chars must be escaped on the public highscores page."""
# #         user_id = _insert_user(f"xss_hs_{hash(payload)}", payload)
# #         _insert_highscore(user_id)
# #         resp = client.get("/highscores")
# #         assert resp.status_code == 200
# #         assert payload.encode() not in resp.data, (  # Expected: payload not in raw HTML
# #             f"Raw XSS payload found unescaped in /highscores: {payload!r}"
# #         )




    # @pytest.mark.xfail(
    #     reason=(
    #         "Known security bug: submitted riddle_index from POST form is used to look "
    #         "up the answer, allowing a player to skip riddles by posting riddle_index=9 "
    #         "with the correct last answer. The session index should be authoritative."
    #     ),
    #     strict=True,
    # )
    # def test_riddle_index_session_is_authoritative(self, client, regular_user):
    #     """Posting riddle_index=9 when session has riddle_index=0 must be rejected.

    #     Currently marked xfail: the app uses the POSTed index, not the session
    #     index, so this test intentionally fails to document the bug.
    #     """
    #     login(client, regular_user)
    #     _start_game(client)  # sets session riddle_index = 0

    #     # Attempt to jump directly to riddle 9 (the last one) with the correct answer
    #     resp = _post_answer(client, 9, ANSWERS[9])

    #     # Should NOT redirect to congratulations — the session says we are on riddle 0
    #     location = resp.headers.get("Location", "")
    #     assert "congratulations" not in location and resp.status_code != 302, (  # Expected: no redirect to congratulations
    #         "App allowed skipping to the last riddle via manipulated riddle_index"
    #     )


# =============================================================================
# 8. Admin Riddle Management Tests
# =============================================================================


class TestAdminRiddles:
    """Access control and CRUD tests for the admin riddle management routes.

    Validates that:
    - Unauthenticated users are redirected to the login page (not served admin content)
    - Authenticated non-admin users receive 403 Forbidden (role enforcement)
    - Admin users can view, add, and delete riddles
    - Server-side validation rejects empty fields and enforces the minimum riddle count
    """

    def test_admin_view_riddles(self, client, admin_user):
        """Admin user can access the riddle management page and see riddle data.

        Setup:  Log in as a user with role='admin'. The DB is seeded with 10 riddles.
        Action: GET /admin/riddles.
        Expect: 200 OK with HTML containing riddle content from the database.
                This confirms the admin role check passes and the template renders
                with actual riddle data (not just an empty page).
        """
        login(client, admin_user)
        resp = client.get("/admin/riddles")
        assert resp.status_code == 200, (
            f"Expected 200 OK for admin user, got {resp.status_code}"
        )
        # Verify the page actually contains riddle data from the seeded DB
        html = resp.data.decode()
        assert "Nothing" in html or "River" in html, (
            "Admin riddles page should display seeded riddle answers but none were found"
        )

    def test_admin_add_riddle(self, client, admin_user):
        """Admin can add a new riddle and it persists in the database.

        Setup:  Log in as admin. DB starts with 10 seeded riddles.
        Action: POST /admin/riddles/add with a valid question and answer.
        Expect: 302 redirect back to the riddle management page (successful action).
                The new riddle must exist in the database — verified by querying the
                riddles table directly. The total count must increase from 10 to 11.
        """
        login(client, admin_user)

        # Record initial count
        conn = sqlite3.connect(_db_path)
        count_before = conn.execute("SELECT COUNT(*) FROM riddles").fetchone()[0]
        conn.close()

        resp = client.post(
            "/admin/riddles/add",
            data={"question": "Test question?", "answer": "TestAnswer"},
        )
        assert resp.status_code == 302, (
            f"Expected 302 redirect after adding riddle, got {resp.status_code}"
        )

        # Verify the riddle was actually inserted into the DB
        conn = sqlite3.connect(_db_path)
        row = conn.execute(
            "SELECT * FROM riddles WHERE question = ?", ("Test question?",)
        ).fetchone()
        count_after = conn.execute("SELECT COUNT(*) FROM riddles").fetchone()[0]
        conn.close()
        assert row is not None, "New riddle was not found in the database after POST"
        assert count_after == count_before + 1, (
            f"Riddle count should increase by 1: was {count_before}, now {count_after}"
        )

    def test_admin_add_riddle_empty_fields(self, client, admin_user):
        """Server-side validation rejects riddles with empty question or answer.

        Setup:  Log in as admin.
        Action: POST /admin/riddles/add with (a) empty question, (b) empty answer.
        Expect: 400 Bad Request in both cases. The server must not accept incomplete
                riddles — this is server-side validation (not just HTML 'required').
                Also verify that no new riddle was inserted into the DB.
        """
        login(client, admin_user)

        conn = sqlite3.connect(_db_path)
        count_before = conn.execute("SELECT COUNT(*) FROM riddles").fetchone()[0]
        conn.close()

        # Empty question
        resp = client.post("/admin/riddles/add", data={"question": "", "answer": "X"})
        assert resp.status_code == 400, (
            f"Expected 400 for empty question, got {resp.status_code}"
        )

        # Empty answer
        resp = client.post("/admin/riddles/add", data={"question": "X", "answer": ""})
        assert resp.status_code == 400, (
            f"Expected 400 for empty answer, got {resp.status_code}"
        )

        # Verify nothing was added to the DB
        conn = sqlite3.connect(_db_path)
        count_after = conn.execute("SELECT COUNT(*) FROM riddles").fetchone()[0]
        conn.close()
        assert count_after == count_before, (
            f"Riddle count changed from {count_before} to {count_after} despite invalid input"
        )

    def test_admin_delete_riddle(self, client, admin_user):
        """Admin can delete a riddle when more than 10 exist in the database.

        Setup:  Log in as admin. Insert an extra (11th) riddle directly into the DB
                so that the minimum-10 guard does not block deletion.
        Action: POST /admin/riddles/<id>/delete for the 11th riddle.
        Expect: 302 redirect back to the management page. The deleted riddle must
                no longer exist in the database. The total count must decrease by 1.
        """
        login(client, admin_user)
        # Add an extra riddle so we have 11 (deletion guard requires > 10)
        conn = sqlite3.connect(_db_path)
        conn.execute(
            "INSERT INTO riddles (question, answer) VALUES (?, ?)",
            ("Extra riddle?", "Extra"),
        )
        conn.commit()
        riddle_id = conn.execute(
            "SELECT id FROM riddles WHERE question = ?", ("Extra riddle?",)
        ).fetchone()[0]
        count_before = conn.execute("SELECT COUNT(*) FROM riddles").fetchone()[0]
        conn.close()
        assert count_before == 11, f"Expected 11 riddles before delete, got {count_before}"

        resp = client.post(f"/admin/riddles/{riddle_id}/delete")
        assert resp.status_code == 302, (
            f"Expected 302 redirect after deleting riddle, got {resp.status_code}"
        )

        # Verify the riddle is actually gone from the DB
        conn = sqlite3.connect(_db_path)
        row = conn.execute("SELECT * FROM riddles WHERE id = ?", (riddle_id,)).fetchone()
        count_after = conn.execute("SELECT COUNT(*) FROM riddles").fetchone()[0]
        conn.close()
        assert row is None, "Riddle still exists in DB after deletion"
        assert count_after == count_before - 1, (
            f"Riddle count should decrease by 1: was {count_before}, now {count_after}"
        )

    def test_admin_delete_riddle_minimum_guard(self, client, admin_user):
        """Deletion is blocked with 400 when exactly 10 riddles remain (minimum for a game).

        Setup:  Log in as admin. The DB has exactly 10 seeded riddles.
        Action: POST /admin/riddles/<id>/delete for one of the 10 riddles.
        Expect: 400 Bad Request. The riddle must NOT be deleted and the count must
                remain 10. This guard ensures games can always select 10 riddles —
                without it, a game start would fail.
        """
        login(client, admin_user)
        conn = sqlite3.connect(_db_path)
        riddle_id = conn.execute("SELECT id FROM riddles LIMIT 1").fetchone()[0]
        count_before = conn.execute("SELECT COUNT(*) FROM riddles").fetchone()[0]
        conn.close()
        assert count_before == 10, f"Expected exactly 10 seeded riddles, got {count_before}"

        resp = client.post(f"/admin/riddles/{riddle_id}/delete")
        assert resp.status_code == 400, (
            f"Expected 400 Bad Request, got {resp.status_code}"
        )

        # Verify the riddle was NOT deleted — the guard must have blocked it
        conn = sqlite3.connect(_db_path)
        row = conn.execute("SELECT * FROM riddles WHERE id = ?", (riddle_id,)).fetchone()
        count_after = conn.execute("SELECT COUNT(*) FROM riddles").fetchone()[0]
        conn.close()
        assert row is not None, (
            "Riddle was deleted despite the minimum-10 guard — games would break"
        )
        assert count_after == 10, (
            f"Riddle count changed from 10 to {count_after} — minimum guard failed"
        )

    def test_non_admin_add_riddle(self, client, regular_user):
        """Non-admin user cannot add riddles — role enforcement on POST route.

        Setup:  Log in as a regular (non-admin) user.
        Action: POST /admin/riddles/add with valid riddle data.
        Expect: 403 Forbidden. The server-side is_admin check (SR4) must reject the
                request even though the user is authenticated. Also verify that no
                riddle was actually inserted — the 403 must be a real block, not just
                a response code with a side effect.
        """
        login(client, regular_user)

        conn = sqlite3.connect(_db_path)
        count_before = conn.execute("SELECT COUNT(*) FROM riddles").fetchone()[0]
        conn.close()

        resp = client.post(
            "/admin/riddles/add",
            data={"question": "Hack?", "answer": "Hack"},
        )
        assert resp.status_code == 403, (
            f"Expected 403 Forbidden for non-admin, got {resp.status_code}"
        )

        # Verify nothing was inserted — the 403 must be a real block
        conn = sqlite3.connect(_db_path)
        count_after = conn.execute("SELECT COUNT(*) FROM riddles").fetchone()[0]
        conn.close()
        assert count_after == count_before, (
            f"Riddle count changed from {count_before} to {count_after} — "
            "non-admin was able to insert a riddle despite 403"
        )

    def test_non_admin_delete_riddle(self, client, regular_user):
        """Non-admin user cannot delete riddles — role enforcement on POST route.

        Setup:  Log in as a regular (non-admin) user. Add an 11th riddle so the
                minimum guard is not the reason for blocking (we want to test role
                enforcement specifically, not the count guard).
        Action: POST /admin/riddles/<id>/delete.
        Expect: 403 Forbidden. The server-side is_admin check (SR4) must reject the
                request. Also verify the riddle still exists in the DB after the
                attempt — confirming the 403 truly blocked the operation.
        """
        login(client, regular_user)

        # Add an 11th riddle so the min-10 guard is not a factor
        conn = sqlite3.connect(_db_path)
        conn.execute(
            "INSERT INTO riddles (question, answer) VALUES (?, ?)",
            ("Target riddle?", "Target"),
        )
        conn.commit()
        riddle_id = conn.execute(
            "SELECT id FROM riddles WHERE question = ?", ("Target riddle?",)
        ).fetchone()[0]
        conn.close()

        resp = client.post(f"/admin/riddles/{riddle_id}/delete")
        assert resp.status_code == 403, (
            f"Expected 403 Forbidden for non-admin, got {resp.status_code}"
        )

        # Verify the riddle was NOT deleted
        conn = sqlite3.connect(_db_path)
        row = conn.execute("SELECT * FROM riddles WHERE id = ?", (riddle_id,)).fetchone()
        conn.close()
        assert row is not None, (
            "Riddle was deleted by non-admin user — role enforcement failed"
        )

    def test_non_admin_view_riddles(self, client, regular_user):
        """regular user can NOT access the riddle management page and see riddle data.

        Setup:  Log in as a user with role='user'. The DB is seeded with 10 riddles.
        Action: GET /admin/riddles.
        Expect: 403 FORBIDDEN with HTML containing NO riddle content from the database.
                This confirms the admin role check passes and the template renders
                with actual riddle data (not just an empty page).
        """
        login(client, regular_user)
        resp = client.get("/admin/riddles")
        assert resp.status_code == 403, (
            f"Expected 403 OK for regular user, got {resp.status_code}"
        )
        # Verify the page actually contains riddle data from the seeded DB
        html = resp.data.decode()
        assert "Nothing" not in html or "River" not in html, (
            "regular user riddles page should NOT display seeded riddle answers"
        )