"""One-time terminal bootstrap for the first CampusFix administrator.

Usage (from the project root):

    python scripts/create_admin.py

You are prompted for the administrator's name and @campus.ac.in email, then
for the password twice with ``getpass`` (not echoed). Passwords are never
accepted as command-line arguments, never hardcoded, and neither passwords
nor password hashes are ever printed.

The command refuses to run once any administrator exists. Public
registration in the web app can only create student accounts.

For automated setups, ``--non-interactive`` reads three lines from standard
input instead: name, email, password (e.g. piped from a secrets manager).
"""

import argparse
import getpass
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import database.db as db  # noqa: E402
from auth.authentication import (  # noqa: E402
    AuthError,
    CAMPUS_EMAIL_DOMAIN,
    MIN_PASSWORD_LENGTH,
    bootstrap_admin,
    validate_campus_email,
    validate_name,
)


def _prompt_interactive():
    name = validate_name(input("Administrator name: "))
    email = validate_campus_email(
        input(f"Administrator email (@{CAMPUS_EMAIL_DOMAIN}): ")
    )
    password = getpass.getpass(
        f"Password (min {MIN_PASSWORD_LENGTH} characters, not echoed): "
    )
    confirmation = getpass.getpass("Confirm password: ")

    if password != confirmation:
        raise AuthError("Passwords do not match.")

    return name, email, password


def _read_non_interactive():
    lines = [sys.stdin.readline().rstrip("\r\n") for _ in range(3)]
    return lines[0], lines[1], lines[2]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Create the first CampusFix administrator account."
    )
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite database path (default: ./campusfix.db)",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Read name, email and password as three lines from stdin",
    )
    args = parser.parse_args(argv)

    if args.db:
        db.DB_PATH = Path(args.db).resolve()

    try:
        # Creates the database if needed and applies pending migrations.
        db.initialize_database()

        if db.admin_exists():
            raise AuthError(
                "An administrator account already exists. The bootstrap "
                "command can only be used once."
            )

        if args.non_interactive:
            name, email, password = _read_non_interactive()
        else:
            name, email, password = _prompt_interactive()

        user = bootstrap_admin(name, email, password)

    except AuthError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.", file=sys.stderr)
        return 1

    print(
        f"Administrator account created for {user['name']} "
        f"<{user['email']}> (user_id {user['user_id']})."
    )
    print("Sign in through the CampusFix login page.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
