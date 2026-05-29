# Riddle Me This

A Flask-based riddle game where players answer 10 randomly selected riddles, earn points, and compete on a highscore leaderboard. Built as part of the COMP3310 security-focused group project by team 'Byte-Me'.

## Features

- GitHub OAuth authentication (no passwords stored)
- 10 random riddles per game, 3 guesses per riddle, max 3 points each
- Hint system — reveals the first letter of the answer at a 1-point cost
- Persistent highscore leaderboard backed by SQLite
- Admin panel for managing riddles and highscores
- Role-based access control (user / admin)
- CSRF protection, rate limiting, and secure session management

## Security Measures

- **CSRF protection** on all POST forms via Flask-WTF `CSRFProtect`
- **Rate limiting** on the OAuth login endpoint (10 requests/minute per IP)
- **Parameterised queries** for all database operations (no string concatenation)
- **Signed server-side sessions** with secret key loaded from environment variables
- **RBAC** — admin role verified server-side on every privileged route
- **Custom error pages** (429, 500) that do not leak internal details
- **OAuth state parameter** managed by Flask-Dance to prevent authorisation CSRF

## Authentication & RBAC

Authentication uses GitHub OAuth via Flask-Dance. On first login, a user record is created in the database with the `user` role. The admin role is assigned automatically when the authenticated GitHub user's ID matches the `ADMIN_GITHUB_ID` environment variable. Role is re-evaluated on every login.

**Access levels:**

| Role | Access |
|------|--------|
| Not logged in | Home page, highscores |
| Logged-in user | Play game, view highscores, logout |
| Admin | All user access + admin panel (manage riddles & highscores) |

## Database

The app uses SQLite (migrated from the original text-file storage). The database is initialised automatically on first run via `db.init_db()`, which creates three tables:

- **users** — `id`, `github_id`, `username`, `email`, `role`, `created_at`
- **highscores** — `id`, `user_id` (FK to users), `score`, `created_at`
- **riddles** — `id`, `question`, `answer`

Riddles are seeded from the legacy `data/-riddles.txt` and `data/-answers.txt` files if present, otherwise from built-in fallback data.

## Feature 1: Admin Riddle Management

Admins can add new riddles and delete existing ones from the `/admin/riddles` panel. A minimum of 10 riddles is enforced — deletions are blocked if the count would drop below this threshold, ensuring a full game can always be started.

## Feature 2: Hint System

During gameplay, players can request a hint that reveals the first letter of the answer. Using a hint costs 1 point from that riddle's score. The hint state resets when advancing to the next riddle. Only one hint is available per riddle.



## Testing

```bash
pytest -v
```

Tests use a temporary SQLite database and bypass GitHub OAuth via a test-only login route. CSRF is disabled by default in the test client and enabled selectively for CSRF-specific tests.
