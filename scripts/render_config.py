#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.auth import hash_password  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a secure Bite Hunt configuration")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--admin-username", default="admin")
    parser.add_argument("--base-url", default="http://localhost")
    parser.add_argument(
        "--trusted-host",
        action="append",
        dest="trusted_hosts",
        help="Allowed Host header. Repeat for multiple hosts; defaults to the base URL host.",
    )
    parser.add_argument("--password-stdin", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def read_password(from_stdin: bool) -> str:
    if from_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = getpass.getpass("Administrator password (12+ characters): ")
        confirmation = getpass.getpass("Confirm administrator password: ")
        if password != confirmation:
            raise SystemExit("Passwords do not match.")
    if len(password) < 12:
        raise SystemExit("Administrator password must contain at least 12 characters.")
    return password


def main() -> int:
    args = parse_args()
    if args.output.exists() and not args.force:
        raise SystemExit(f"Refusing to overwrite existing configuration: {args.output}")
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,80}", args.admin_username):
        raise SystemExit("Administrator username contains unsupported characters.")

    parsed = urlparse(args.base_url)
    default_host = parsed.hostname or "*"
    trusted_hosts = list(dict.fromkeys((args.trusted_hosts or [default_host]) + ["127.0.0.1", "localhost"]))
    if any(not re.fullmatch(r"\*|[A-Za-z0-9_.:-]+", host) for host in trusted_hosts):
        raise SystemExit("A trusted host contains unsupported characters.")

    password = read_password(args.password_stdin)
    data_dir = args.data_dir.expanduser().resolve()
    config = {
        "app": {
            "name": "Bite Hunt Careers",
            "environment": "production",
            "base_url": args.base_url,
            "session_secret": secrets.token_urlsafe(48),
            "cookie_secure": parsed.scheme == "https",
            "trusted_hosts": trusted_hosts,
        },
        "storage": {
            "database_path": str(data_dir / "data" / "bite-hunt.db"),
            "resume_dir": str(data_dir / "uploads"),
            "max_upload_mb": 10,
            "allowed_extensions": ["pdf", "doc", "docx"],
        },
        "admin": {
            "username": args.admin_username,
            "password_hash": hash_password(password),
        },
        "security": {
            "application_limit_per_hour": 8,
            "login_limit_per_15_minutes": 10,
        },
        "ai": {
            "enabled": True,
            "config_path": str(args.output.parent / "deepseek.json"),
            "timeout_seconds": 90,
            "max_resume_characters": 50000,
        },
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(config, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, args.output)
    finally:
        temporary.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
