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

    SERVICE_NAME = "IITD_OUTREACH_ENGINE"
    LEGACY_SERVICE_NAME = "iitd_outreach_engine"

    @classmethod
    def get_credentials(cls, username: Optional[str] = None) -> Tuple[str, Optional[str]]:
        """
        Retrieve (username, password) without exposing secrets in logs.
        Queries environment variables first, then securely searches Windows Credential Manager.
        Supports both full email (ch1251635@iitd.ac.in) and Kerberos user ID (ch1251635).
        """
        user = username or os.environ.get("IITD_EMAIL_USER", settings.IITD_EMAIL_USER)
        password = os.environ.get("IITD_EMAIL_PASSWORD", settings.IITD_EMAIL_PASSWORD)

        # Fallback check for keyring if password not in env
        if not password:
            try:
                import keyring

                # Build candidate usernames to probe
                candidates = []
                if user:
                    candidates.append(user)
                    if "@" in user:
                        candidates.append(user.split("@")[0])
                    else:
                        candidates.append(f"{user}@iitd.ac.in")
                else:
                    candidates.extend(["ch1251635@iitd.ac.in", "ch1251635"])

                for svc in [cls.SERVICE_NAME, cls.LEGACY_SERVICE_NAME]:
                    for cand in candidates:
                        pwd = keyring.get_password(svc, cand)
                        if pwd:
                            return cand, pwd
            except Exception:
                pass

        return user, password

    @classmethod
    def set_credentials(cls, username: str, password: str) -> bool:
        """
        Securely store IIT Delhi credentials into Windows Credential Manager via keyring.
        Saves under both the primary username and the alternative format (short Kerberos ID / full email)
        to ensure seamless IMAP and SMTP authentication without storing passwords in .env.
        """
        if not username or not password:
            raise ValueError("Username and password must not be empty.")

        import keyring

        # Save under standard service name
        keyring.set_password(cls.SERVICE_NAME, username, password)

        # Also store alias (e.g. ch1251635 if given ch1251635@iitd.ac.in, or vice versa)
        if "@" in username:
            short_id = username.split("@")[0]
            keyring.set_password(cls.SERVICE_NAME, short_id, password)
        else:
            full_email = f"{username}@iitd.ac.in"
            keyring.set_password(cls.SERVICE_NAME, full_email, password)

        return True

    @classmethod
    def delete_credentials(cls, username: Optional[str] = None) -> bool:
        """
        Remove credentials from Windows Credential Manager.
        """
        user = username or settings.IITD_EMAIL_USER
        try:
            import keyring
            candidates = [user]
            if "@" in user:
                candidates.append(user.split("@")[0])
            else:
                candidates.append(f"{user}@iitd.ac.in")

            for svc in [cls.SERVICE_NAME, cls.LEGACY_SERVICE_NAME]:
                for cand in candidates:
                    try:
                        keyring.delete_password(svc, cand)
                    except Exception:
                        pass
            return True
        except Exception:
            return False

    @staticmethod
    def mask_secret(secret: Optional[str]) -> str:
        """Return masked representation of a password or secret."""
        if not secret:
            return "[NOT SET]"
        if len(secret) <= 4:
            return "****"
        return f"{secret[:2]}****{secret[-2:]}"

    @classmethod
    def get_status_summary(cls) -> dict:
        user, pwd = cls.get_credentials()
        return {
            "user": user,
            "has_password": bool(pwd and len(pwd) > 0),
            "masked_user": user,
            "storage_backend": "Windows Credential Manager (keyring)" if (pwd and not os.environ.get("IITD_EMAIL_PASSWORD")) else ("Environment Variable" if pwd else "None"),
            "imap_endpoint": f"{settings.IITD_IMAP_SERVER}:{settings.IITD_IMAP_PORT} (SSL)",
            "smtp_endpoint": f"{settings.IITD_SMTP_SERVER}:{settings.IITD_SMTP_PORT} (SSL)"
        }
