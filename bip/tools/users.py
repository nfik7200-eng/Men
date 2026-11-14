"""Create and manage users from the command line: ``uv run python -m bip.tools.users --help``.

This is the only way to create the first administrator. There is no open registration endpoint.
The password comes from ``BIP_NEW_PASSWORD``, a file named by ``BIP_NEW_PASSWORD_FILE``, or a
hidden prompt; it is never accepted as a command-line argument, so it cannot end up in shell
history or the process list.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from bip.config.settings import load_settings
from bip.core.exceptions import BIPError
from bip.db.engine import create_db_engine
from bip.db.repository import Repository
from bip.security.auth import AuthService
from bip.security.passwords import password_problem


def _say(message: str, *, error: bool = False) -> None:
    print(message, file=sys.stderr if error else sys.stdout)


def read_password(environ: Mapping[str, str], username: str) -> str:
    direct = environ.get("BIP_NEW_PASSWORD")
    file_ref = environ.get("BIP_NEW_PASSWORD_FILE")
    if direct and file_ref:
        raise SystemExit("set only one of BIP_NEW_PASSWORD and BIP_NEW_PASSWORD_FILE")
    if file_ref:
        password = Path(file_ref).read_text(encoding="utf-8").rstrip("\r\n")
    elif direct:
        password = direct
    else:
        password = getpass.getpass("Password: ")
        if getpass.getpass("Repeat: ") != password:
            raise SystemExit("the passwords differ")
    problem = password_problem(password, username)
    if problem:
        raise SystemExit(problem)
    return password


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(prog="bip.tools.users", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="create a user")
    create.add_argument("username")
    create.add_argument("--role", choices=("viewer", "analyst", "admin"), default="admin")
    sub.add_parser("list", help="list users")
    reset = sub.add_parser("set-password", help="set a user's password and end their sessions")
    reset.add_argument("username")
    args = parser.parse_args(argv)

    settings = load_settings()
    engine = create_db_engine(settings)
    try:
        service = AuthService(Repository(engine), settings.auth)
        if args.command == "list":
            for user in service.list_users():
                state = "enabled" if user.enabled else "disabled"
                _say(f"{user.id}\t{user.username}\t{user.role}\t{state}")
            return 0
        password = read_password(env, args.username)
        try:
            if args.command == "create":
                user = service.create_user(args.username, password, args.role, actor="cli")
                _say(f"created {user.username} ({user.role})")
            else:
                match = [u for u in service.list_users() if u.username == args.username.lower()]
                if not match:
                    _say(f"no such user: {args.username}", error=True)
                    return 1
                service.set_password(match[0].id, password, actor="cli")
                _say(f"password set for {match[0].username}")
        except BIPError as exc:
            _say(str(exc), error=True)
            return 1
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
