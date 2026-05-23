# Grade: D — parses and runs, but riddled with OWASP-class security vulnerabilities.
#
# Run: code-explain --analyze e2e_tests/02_python_security_vulns.py

import hashlib
import os
import pickle
import sqlite3
import subprocess

# CWE-798: Hardcoded credentials
SECRET_KEY = "hunter2"
DB_ADMIN_PASSWORD = "P@ssw0rd123!"
DB_PATH = "app.db"


def get_user(username: str) -> dict:
    """Fetch a user record — OWASP A03: SQL Injection."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # BAD: f-string directly interpolates user input into SQL
    cur.execute(f"SELECT * FROM users WHERE username = '{username}'")
    row = cur.fetchone()
    conn.close()
    return row


def run_report(report_name: str) -> str:
    """Generate a report — OWASP A03: OS Command Injection."""
    # BAD: shell=True with unsanitised user-controlled string
    result = subprocess.run(
        f"generate_report.sh {report_name}",
        shell=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def evaluate_formula(formula: str) -> float:
    """Evaluate a user-supplied math formula — OWASP A03: Code Injection."""
    # BAD: eval() executes arbitrary Python
    return eval(formula)


def restore_session(blob: bytes) -> object:
    """Deserialise a session cookie — OWASP A08: Insecure Deserialisation."""
    # BAD: pickle.loads() with untrusted bytes can execute arbitrary code
    return pickle.loads(blob)


def hash_password(password: str) -> str:
    """Hash a password before storing — OWASP A02: Cryptographic Failure."""
    # BAD: MD5 is broken for passwords; use bcrypt/scrypt/argon2 instead
    return hashlib.md5(password.encode()).hexdigest()


def read_file(filename: str) -> str:
    """Return file contents — OWASP A01: Path Traversal."""
    # BAD: no canonicalisation; "../../etc/passwd" works
    base = "/var/app/uploads"
    full_path = os.path.join(base, filename)
    with open(full_path) as f:
        return f.read()


def login(username: str, password: str) -> bool:
    """Validate credentials."""
    stored = get_user(username)
    if stored is None:
        # BAD: leaks whether the username exists via different error messages
        raise ValueError(f"User '{username}' not found")
    # BAD: comparing MD5 hash directly; also no timing-safe compare
    return stored["password_hash"] == hash_password(password)
