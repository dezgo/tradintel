#!/usr/bin/env python3
"""
Password hash generator for TradingBot authentication.

This script generates a bcrypt password hash that can be used in the .env file.
Run this script to generate a new password hash for AUTH_PASSWORD_HASH.

Usage:
    python3 scripts/generate_password_hash.py
"""

import getpass
import sys

try:
    import bcrypt
except ImportError:
    print("Error: bcrypt is not installed.")
    print("Install it with: pip install bcrypt")
    sys.exit(1)


def generate_hash(password: str) -> str:
    """Generate a bcrypt hash from a password."""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def main():
    print("=" * 60)
    print("TradingBot Password Hash Generator")
    print("=" * 60)
    print()
    print("This will generate a bcrypt hash for your password.")
    print("Copy the hash to your .env file as AUTH_PASSWORD_HASH.")
    print()

    # Get password from user (hidden input)
    password = getpass.getpass("Enter password: ")

    if not password:
        print("Error: Password cannot be empty")
        sys.exit(1)

    # Confirm password
    password_confirm = getpass.getpass("Confirm password: ")

    if password != password_confirm:
        print("Error: Passwords do not match")
        sys.exit(1)

    # Generate hash
    print("\nGenerating hash...")
    password_hash = generate_hash(password)

    print("\n" + "=" * 60)
    print("Password hash generated successfully!")
    print("=" * 60)
    print("\nAdd this to your .env file:\n")
    print(f"AUTH_PASSWORD_HASH={password_hash}")
    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
