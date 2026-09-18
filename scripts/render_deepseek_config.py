#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the private DeepSeek configuration"
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--model", default="deepseek-v4-pro")
    parser.add_argument("--max-output-tokens", type=int, default=6000)
    parser.add_argument("--api-key-stdin", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def read_api_key(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().strip()
    return getpass.getpass("DeepSeek API key (leave blank to configure later): ").strip()


def main() -> int:
    args = parse_args()
    if args.output.exists() and not args.force:
        raise SystemExit(f"Refusing to overwrite existing configuration: {args.output}")
    parsed = urlparse(args.base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise SystemExit("DeepSeek base URL must be an HTTPS origin.")
    if not args.model.strip():
        raise SystemExit("DeepSeek model cannot be empty.")
    if not 1_000 <= args.max_output_tokens <= 32_000:
        raise SystemExit("Max output tokens must be between 1000 and 32000.")

    config = {
        "provider": "deepseek",
        "base_url": args.base_url.rstrip("/"),
        "endpoint_path": "/chat/completions",
        "api_key": read_api_key(args.api_key_stdin),
        "model": args.model.strip(),
        "max_output_tokens": args.max_output_tokens,
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
