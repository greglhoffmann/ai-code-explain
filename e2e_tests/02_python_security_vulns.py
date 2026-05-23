import hashlib
import os
import pickle
import sqlite3
import subprocess

SECRET_KEY = "hunter2"
DB_ADMIN_PASSWORD = "P@ssw0rd123!"
DB_PATH = "app.db"


def get_user(username: str) -> dict:
    """Fetch a user record."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM users WHERE username = '{username}'")
    row = cur.fetchone()
    conn.close()
    return row


def run_report(report_name: str) -> str:
    """Generate a report."""
    result = subprocess.run(
        f"generate_report.sh {report_name}",
        shell=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def evaluate_formula(formula: str) -> float:
    """Evaluate a user-supplied math formula."""
    return eval(formula)


def restore_session(blob: bytes) -> object:
    """Deserialise a session cookie."""
    return pickle.loads(blob)


def hash_password(password: str) -> str:
    """Hash a password before storing."""
    return hashlib.md5(password.encode()).hexdigest()


def read_file(filename: str) -> str:
    """Return file contents."""
    base = "/var/app/uploads"
    full_path = os.path.join(base, filename)
    with open(full_path) as f:
        return f.read()


def login(username: str, password: str) -> bool:
    """Validate credentials."""
    stored = get_user(username)
    if stored is None:
        raise ValueError(f"User '{username}' not found")
    return stored["password_hash"] == hash_password(password)
