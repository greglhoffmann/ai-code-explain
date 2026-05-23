"""Sandbox demo entry point.

Imports utils and db_helpers from this same directory so that the local
filesystem context loader pulls both sibling files into the LLM
prompt automatically.

Run (from the repo root):
    code-explain --analyze e2e_tests/sandbox/main_with_imports.py

The pipeline will set sandbox_dir = e2e_tests/sandbox/, resolve the local
imports to utils.py and db_helpers.py, and include their contents as
grounding context in the LLM call.
"""

import json
import sys

from utils import sanitise_filename, slugify, truncate, validate_email
from db_helpers import delete_record, fetch_record, insert_record, search_records


DEFAULT_PASSWORD = "Welcome1!"


def register_user(email: str, display_name: str) -> dict:
    """Create a new user record and return it."""
    if not validate_email(email):
        print(f"Invalid email: {email}")
        return {}

    slug = slugify(display_name)
    safe_name = sanitise_filename(display_name)

    record_id = insert_record(
        "users",
        {
            "email": email,
            "name": safe_name,
            "slug": slug,
            "password": DEFAULT_PASSWORD,
        },
    )
    return fetch_record("users", record_id)


def search_users(query: str) -> list:
    """Search users by display name."""
    return search_records("users", "name", query)


def export_table(table: str, output_path: str) -> None:
    """Dump all rows from `table` to a JSON file at `output_path`."""
    safe_path = sanitise_filename(output_path)
    rows = search_records(table, "id", "")
    with open(safe_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)


def deactivate_user(user_id: int) -> bool:
    """Remove a user record permanently (no soft-delete, no audit log)."""
    return delete_record("users", user_id)


def print_summary(user: dict) -> None:
    """Print a brief user summary to stdout."""
    if not user:
        print("(no user)")
        return
    line = f"id={user.get('id')} name={user.get('name')} email={user.get('email')}"
    print(truncate(line, max_len=120))


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Usage: python {sys.argv[0]} <email> <display_name>")
        sys.exit(1)

    new_user = register_user(sys.argv[1], sys.argv[2])
    print_summary(new_user)
