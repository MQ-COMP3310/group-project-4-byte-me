import os

# SR5: Load secrets from .env file — never hardcode credentials in source (CWE-798)
from dotenv import load_dotenv
load_dotenv()

from flask import Flask, render_template, redirect, request, url_for, session, abort, flash
from flask_dance.contrib.github import make_github_blueprint, github
from flask_dance.consumer import oauth_authorized, oauth_error
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import (
    LoginManager, UserMixin,
    login_user, logout_user, login_required, current_user,
)
from flask_wtf import CSRFProtect

import db as database

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__)

# SR5: Secret key loaded from environment variable — never hardcoded (CWE-798)
app.config["SECRET_KEY"] = os.environ["SECRET_KEY"]

app.config["DEBUG"] = os.environ.get("FLASK_DEBUG", "false").lower() == "true"

app.config["WTF_CSRF_ENABLED"] = True

# SR: CSRF token required on all state-changing POST forms via Flask-WTF CSRFProtect
csrf = CSRFProtect(app)

# SR8: Rate limiting — mitigates DoS on OAuth initiation endpoint (OWASP A02/A07)
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=[],   # no global default; limit only the login route
)

login_manager = LoginManager(app)
login_manager.login_view = "index"

# SR7: OAuth state parameter prevents CSRF on the authorisation flow
#           (flask-dance manages the state parameter automatically)
github_bp = make_github_blueprint(
    client_id=os.environ.get("GITHUB_OAUTH_CLIENT_ID"),
    client_secret=os.environ.get("GITHUB_OAUTH_CLIENT_SECRET"),
    redirect_to="welcome",
)

#SR2: using git as a trusted provider instead of relying on username/passwords
app.register_blueprint(github_bp, url_prefix="/login")

#SR8: 10 requests/minute per IP on login — prevents brute-force/DoS 
# also, crazy that you can just put "10 per minute" and the limitter library gets what im saying!

# so used the following function to see what the actual route was that flask was using from the blueprint
# print("VIEW FUNCTIONS:", app.view_functions.keys())

# now the whole view function is wrapped by the limitter
app.view_functions["github.login"] = limiter.limit("10 per minute")(
    app.view_functions["github.login"]
)


app.teardown_appcontext(database.close_db)


# ---------------------------------------------------------------------------
# User model -- falsk login
# ---------------------------------------------------------------------------

class User(UserMixin):
    def __init__(self, id, github_id, username, email, role):
        self.id = id
        self.github_id = github_id
        self.username = username
        self.email = email
        self.role = role

    @property
    def is_admin(self):
        return self.role == "admin"


@login_manager.user_loader
def load_user(user_id):
    row = database.get_db().execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if row is None:
        app.logger.warning(f"no user with userId: {user_id}")
        return None
    return User(row["id"], row["github_id"], row["username"], row["email"], row["role"])


# ---------------------------------------------------------------------------
# OAuth callback 
# ---------------------------------------------------------------------------

#All error handling is part of SR7

@app.errorhandler(429)
def ratelimit_handler(e):
    # SR9: Log for monitoring; return safe page without leaking internals (OWASP A09)
    app.logger.warning("Rate limit exceeded from %s", request.remote_addr)
    return render_template("errors/429.html"), 429


@app.errorhandler(500)
def internal_error(e):
    app.logger.error("Internal server error: %s", str(e))
    return render_template("errors/500.html"), 500


@oauth_authorized.connect_via(github_bp)
def github_logged_in(blueprint, token):
    if not token:
        # SR7: Flash error without leaking OAuth/API details to client 
        flash("Login failed: no token received from GitHub. Please try again.", "danger")
        return False

    resp = blueprint.session.get("/user")
    if not resp.ok:
        # SR7: Flash error without leaking OAuth/API details to client 
        flash(
            "Login failed: could not retrieve your GitHub profile. "
            "GitHub may be temporarily unavailable — please try again later.",
            "danger",
        )
        return False

    user_info = resp.json()
    github_id = user_info["id"]
    # SR6: Only GitHub ID and public profile stored — minimal data principle (GDPR / privacy)

    username = user_info.get("login", "")
    email = user_info.get("email", "")

    user_row = database.get_or_create_user(github_id, username, email)
    user = User(
        user_row["id"], user_row["github_id"],
        user_row["username"], user_row["email"], user_row["role"],
    )

    # SR5 Flask-Login regenerates session on login 
    login_user(user)

    #SR6 Return False so flask-dance does not store the OAuth token in the session
    return False


@oauth_error.connect_via(github_bp)
def github_oauth_error(blueprint, error, error_description, error_uri):
    # SR7: Full error logged server-side; only safe summary shown to user 
    app.logger.warning(
        "GitHub OAuth error: error=%s description=%s uri=%s",
        error, error_description, error_uri,
    )
    if error == "access_denied":
        flash("Login cancelled — you did not authorise access on GitHub.", "warning")
    else:
        flash(
            "GitHub login error: please try again. "
            "If the problem persists, GitHub may be experiencing issues.",
            "danger",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _riddles():
    with open("data/-riddles.txt", "r") as f:
        return f.read().splitlines()


def _answers():
    with open("data/-answers.txt", "r") as f:
        return f.read().splitlines()


# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def index():
    if current_user.is_authenticated:
        return redirect(url_for("welcome"))
    return render_template("index.html", page_title="Home")


@app.route("/highscores")
def highscores():
    rows = database.get_highscores()
    usernames_and_scores = [(row["username"], row["score"]) for row in rows]
    return render_template(
        "highscores.html",
        page_title="Highscores",
        usernames_and_scores=usernames_and_scores,
    )


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/logout")
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Game routes (all require login)
# ---------------------------------------------------------------------------

@app.route("/welcome", methods=["GET", "POST"])
@login_required
def welcome():
    if request.method == "POST":
        # SR3: Reset game state stored in the signed session cookie
        session["riddle_index"] = 0
        session["guesses"] = []
        session["score"] = 0
        return redirect(url_for("game"))
    return render_template("welcome.html", username=current_user.username)


@app.route("/game", methods=["GET", "POST"])
@login_required
def game():
    # SR1: redirect to welcome if no game has been started
    if "riddle_index" not in session:
        return redirect(url_for("welcome"))

    riddle_list = _riddles()
    answers = _answers()

    riddle_index = session.get("riddle_index", 0)
    guesses = session.get("guesses", [])
    score = session.get("score", 0)

    #riddle_index validated against riddles list length before use to prevent IndexError 
    if riddle_index >= len(riddle_list):
        return redirect(url_for("congrats"))

    if request.method == "POST":
        submitted_index = request.form.get("riddle_index", "")
        try:
            submitted_index = int(submitted_index)
        except ValueError:
            abort(400)

        # riddle_index validated against riddles list length — prevents IndexError
        if submitted_index < 0 or submitted_index >= len(riddle_list):
            abort(400)

        user_response = request.form.get("answer", "").title()

        if answers[submitted_index] == user_response:
            # Correct — round score decreases by one per wrong guess (max 3)
            round_score = 3 - len(guesses)
            score += round_score
            riddle_index = submitted_index + 1

            if riddle_index >= len(riddle_list):
                # Final riddle answered — persist score and redirect to congrats
                session["score"] = score
                session["riddle_index"] = riddle_index
                database.add_highscore(current_user.id, score)
                return redirect(url_for("congrats"))

            # Advance to next riddle; clear per-riddle guesses
            session["riddle_index"] = riddle_index
            session["guesses"] = []
            session["score"] = score
            guesses = []
        else:
            # Wrong answer — append and check if attempts exhausted
            guesses.append(user_response)
            session["guesses"] = guesses
            if len(guesses) >= 3:
                return redirect(url_for("gameover"))

    remaining_attempts = 3 - len(session.get("guesses", []))
    return render_template(
        "game.html",
        riddle_index=session.get("riddle_index", 0),
        riddles=riddle_list,
        attempts=session.get("guesses", []),
        remaining_attempts=remaining_attempts,
        score=session.get("score", 0),
    )


@app.route("/gameover", methods=["GET", "POST"])
@login_required
def gameover():
    if request.method == "POST":
        return redirect(url_for("welcome"))

    # Record the score (even 0) — every attempt is persisted, win or lose
    score = session.get("score", 0)
    database.add_highscore(current_user.id, score)

    session.pop("riddle_index", None)
    session.pop("guesses", None)
    session.pop("score", None)

    return render_template("gameover.html", username=current_user.username)


@app.route("/congratulations", methods=["GET", "POST"])
@login_required
def congrats():
    score = session.get("score", 0)

    if request.method == "POST":
        return redirect(url_for("highscores"))

    return render_template(
        "congratulations.html",
        username=current_user.username,
        score=score,
    )


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------

@app.route("/admin")
@login_required
def admin():
    # SR4: Role checked server-side before every admin operation — no client-supplied role claim trusted
    if not current_user.is_admin:
        abort(403)
    rows = database.get_highscores()
    return render_template("admin.html", highscores=rows)


@app.route("/admin/highscores/<int:entry_id>/delete", methods=["POST"])
@login_required
def admin_delete_highscore(entry_id):
    # SR4: Role checked server-side before every admin operation — no client-supplied role claim trusted
    if not current_user.is_admin:
        abort(403)
    database.delete_highscore(entry_id)
    return redirect(url_for("admin"))


@app.route("/admin/highscores/<int:entry_id>/edit", methods=["POST"])
@login_required
def admin_edit_highscore(entry_id):
    # SR4: Role checked server-side before every admin operation — no client-supplied role claim trusted
    if not current_user.is_admin:
        abort(403)
    # get the score and then make it an int, if value error through a 400
    new_score = request.form.get("score", "")
    try:
        new_score = int(new_score)
    except ValueError:
        abort(400)
    if 0 <= new_score and new_score <= 30:
        database.update_highscore(entry_id, new_score)
    else: 
        app.logger.error("value needs to be between 0 and 30")
    return redirect(url_for("admin"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    database.init_db()
    ip = "127.0.0.1"
    port = 8000
    app.run(host=ip, port=port, debug=app.config["DEBUG"])
    print("VIEW FUNCTIONS:", app.view_functions.keys())

