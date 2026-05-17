"""CLI script — creates database tables. Safe to re-run (uses CREATE TABLE IF NOT EXISTS)."""
from dotenv import load_dotenv
load_dotenv()

from db import init_db

if __name__ == "__main__":
    init_db()
    print("Database initialised successfully.")
