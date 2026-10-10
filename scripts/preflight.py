#!/usr/bin/env python3
"""Production configuration preflight for Infinite Academy.

Does not print secret values. Run from the project root:
    python scripts/preflight.py --production
"""
from __future__ import annotations

import argparse
import os
import re
import sys

try:
    from dotenv import load_dotenv
except ImportError:  # Allows a clear message when dependencies are not installed.
    load_dotenv = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if load_dotenv:
    load_dotenv(os.path.join(ROOT, ".env"), override=False)

PLACEHOLDERS = ("replace-with", "example.com", "your-", "placeholder", "changeme")


def looks_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    return not lowered or any(marker in lowered for marker in PLACEHOLDERS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production", action="store_true", help="require production settings")
    args = parser.parse_args()

    errors: list[str] = []
    warnings: list[str] = []

    # app.py ignores the generic DATABASE_URL on Render/Railway, so production must set DATABASE_URL_MAIN.
    database_url = os.getenv("DATABASE_URL_MAIN", "").strip()
    if not database_url and not args.production:
        database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        (errors if args.production else warnings).append("DATABASE_URL_MAIN is not configured.")
    elif args.production and not database_url.startswith(("postgresql://", "postgresql+psycopg2://", "postgres://")):
        errors.append("Production database must be PostgreSQL; SQLite is for local testing only.")

    secret_key = os.getenv("SECRET_KEY", "").strip()
    if len(secret_key) < 40 or looks_placeholder(secret_key):
        (errors if args.production else warnings).append("SECRET_KEY should be a unique random value of at least 40 characters.")

    admin_email = os.getenv("ADMIN_EMAIL", "").strip()
    admin_password = os.getenv("ADMIN_PASSWORD", "")
    if not admin_email or "@" not in admin_email:
        (errors if args.production else warnings).append("ADMIN_EMAIL must contain the real first administrator email.")
    if not 12 <= len(admin_password) <= 128 or looks_placeholder(admin_password):
        (errors if args.production else warnings).append("ADMIN_PASSWORD must be a unique password of 12–128 characters, not an example value.")

    vdocipher_secret = os.getenv("VDOCIPHER_API_SECRET", "").strip()
    if not vdocipher_secret or looks_placeholder(vdocipher_secret):
        (errors if args.production else warnings).append("VDOCIPHER_API_SECRET is not configured; protected video playback will not work.")

    support_number = re.sub(r"\D", "", os.getenv("SUPPORT_WHATSAPP_NUMBER", ""))
    if not 7 <= len(support_number) <= 15:
        (errors if args.production else warnings).append("SUPPORT_WHATSAPP_NUMBER should contain 7–15 international digits, without + or spaces.")

    cookie_secure = os.getenv("COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes", "on"}
    if args.production and not cookie_secure:
        errors.append("Set COOKIE_SECURE=true for HTTPS. Render/Railway force secure cookies, but keep this explicitly configured.")

    if errors:
        print("PREFLIGHT: FAILED")
        for item in errors:
            print(f"ERROR: {item}")
    else:
        print("PREFLIGHT: passed the configuration checks requested for this mode.")
    for item in warnings:
        print(f"WARNING: {item}")
    if not args.production:
        print("Mode: local/advisory. Run with --production before a public launch.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
