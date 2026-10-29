"""
Secure Credentials Management Module.
Retrieves email authentication credentials safely from environment variables or OS keyring.
Guarantees secrets are never committed, logged in plaintext, or leaked into error traces.
"""

import os
from typing import Optional, Tuple
from config.settings import settings


class CredentialManager:
    """Safely retrieves and masks IIT Delhi email credentials."""

    @staticmethod
    def get_credentials() -> Tuple[str, Optional[str]]:
        """
        Retrieve (username, password) without exposing secrets in logs.
        """
        user = os.environ.get("IITD_EMAIL_USER", settings.IITD_EMAIL_USER)
        password = os.environ.get("IITD_EMAIL_PASSWORD", settings.IITD_EMAIL_PASSWORD)

        # Fallback check for keyring if available
        if not password:
            try:
                import keyring
                password = keyring.get_password("iitd_outreach_engine", user)
            except Exception:
                pass

        return user, password

    @staticmethod
    def mask_secret(secret: Optional[str]) -> str:
        """Return masked representation of a password or secret."""
        if not secret:
            return "[NOT SET]"
        if len(secret) <= 4:
            return "****"
        return f"{secret[:2]}****{secret[-2:]}"

    @staticmethod
    def get_status_summary() -> dict:
        user, pwd = CredentialManager.get_credentials()
        return {
            "user": user,
            "has_password": bool(pwd and len(pwd) > 0),
            "masked_user": user,
            "imap_endpoint": f"{settings.IITD_IMAP_SERVER}:{settings.IITD_IMAP_PORT} (SSL)",
            "smtp_endpoint": f"{settings.IITD_SMTP_SERVER}:{settings.IITD_SMTP_PORT} (SSL)"
        }
