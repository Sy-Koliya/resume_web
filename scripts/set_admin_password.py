#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.auth import hash_password  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Change the Bite Hunt administrator password")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--username")
    parser.add_argument("--password-stdin", action="store_true")
    args = parser.parse_args()

    path = args.config.expanduser().resolve()
    config = json.loads(path.read_text(encoding="utf-8"))
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = getpass.getpass("New administrator password (12+ characters): ")
        confirmation = getpass.getpass("Confirm new password: ")
        if password != confirmation:
            raise SystemExit("Passwords do not match.")
    if len(password) < 12:
        raise SystemExit("Administrator password must contain at least 12 characters.")

    config.setdefault("admin", {})["password_hash"] = hash_password(password)
    if args.username:
        config["admin"]["username"] = args.username

    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    mode = path.stat().st_mode & 0o777
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(config, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    print("Administrator credentials updated. Restart bite-hunt-careers.service to apply them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
