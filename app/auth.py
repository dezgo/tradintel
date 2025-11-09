"""
Authentication module for Flask-Login.
Handles user authentication with bcrypt password hashing.
"""

import os
import bcrypt
from flask_login import UserMixin


class User(UserMixin):
    """
    Simple user class for Flask-Login.
    Configured via environment variables for single-user authentication.
    """

    def __init__(self, user_id, username):
        self.id = user_id
        self.username = username

    @staticmethod
    def get_configured_user():
        """
        Get the configured user from environment variables.
        Returns a User object if credentials are configured, None otherwise.
        """
        username = os.getenv('AUTH_USERNAME')
        if not username:
            return None

        return User(user_id='1', username=username)

    @staticmethod
    def verify_credentials(username, password):
        """
        Verify username and password against configured credentials.

        Args:
            username: Username to verify
            password: Plain text password to verify

        Returns:
            User object if credentials are valid, None otherwise
        """
        configured_username = os.getenv('AUTH_USERNAME')
        configured_password_hash = os.getenv('AUTH_PASSWORD_HASH')

        if not configured_username or not configured_password_hash:
            return None

        # Check username matches
        if username != configured_username:
            return None

        # Verify password against bcrypt hash
        try:
            if bcrypt.checkpw(password.encode('utf-8'), configured_password_hash.encode('utf-8')):
                return User(user_id='1', username=username)
        except (ValueError, AttributeError):
            return None

        return None

    @staticmethod
    def generate_password_hash(password):
        """
        Generate a bcrypt hash for a password.
        Utility method for creating password hashes.

        Args:
            password: Plain text password

        Returns:
            Bcrypt hash as string
        """
        return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
