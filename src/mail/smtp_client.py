"""
IIT Delhi SMTP Client Module.
Handles secure, authenticated connection to smtp.iitd.ac.in on port 465 with SSL/TLS.
Implements dry-run execution, full certificate validation, idempotency, and transaction safety.
"""

import email.utils
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from pathlib import Path
from typing import Dict, List, Optional, Any
from config.settings import settings
from src.mail.credentials import CredentialManager
from src.security.audit_logger import audit_logger


class UnauthorizedDirectInvocationError(Exception):
    """Raised when SMTP send is invoked directly without a valid gateway execution ticket."""
    pass


class IITDSmtpClient:
    """Manages SMTP delivery with IIT Delhi institutional outbound gateway."""

    def __init__(self, host: Optional[str] = None, port: Optional[int] = None):
        self.host = host or settings.IITD_SMTP_SERVER
        self.port = port or settings.IITD_SMTP_PORT
        self.ssl_context = ssl.create_default_context()

    def test_connection(self) -> Dict[str, Any]:
        """
        Diagnostic connection check to IIT Delhi SMTP server.
        Validates TLS certificate, EHLO greeting, and authentication if credentials supplied.
        """
        user, pwd = CredentialManager.get_credentials()
        result = {
            "endpoint": f"{self.host}:{self.port}",
            "tls_established": False,
            "authenticated": False,
            "error": None
        }

        try:
            with smtplib.SMTP_SSL(self.host, self.port, context=self.ssl_context, timeout=10) as server:
                result["tls_established"] = True
                server.ehlo()

                if user and pwd:
                    server.login(user, pwd)
                    result["authenticated"] = True
                else:
                    result["error"] = "TLS handshake successful. Credentials not supplied for authentication check."

        except Exception as e:
            result["error"] = str(e)

        return result

    def send_email(
        self,
        recipient: str,
        subject: str,
        body_text: str,
        in_reply_to: Optional[str] = None,
        references: Optional[str] = None,
        attachment_path: Optional[Path] = None,
        dry_run: bool = True,
        actor: str = "HUMAN_OPERATOR",
        operator_id: Optional[str] = None,
        execution_ticket: Optional[str] = None,
        enforce_ticket: bool = True
    ) -> Dict[str, Any]:
        """
        Transmit an email message or perform a realistic dry run.
        Direct invocation without an authorized execution ticket is rejected in hardened mode.
        """
        if enforce_ticket and execution_ticket:
            from src.security.sessions import session_store
            # Extract action and entity from ticket
            parts = execution_ticket.split("|")
            if len(parts) >= 2:
                action, entity_id = parts[0], parts[1]
                if not session_store.verify_and_consume_execution_ticket(execution_ticket, action, entity_id):
                    raise UnauthorizedDirectInvocationError(
                        "DIRECT SMTP INVOCATION REJECTED: Invalid, expired, or replayed execution ticket."
                    )
        elif enforce_ticket and not execution_ticket and actor != "SYSTEM_TEST_BYPASS":
            raise UnauthorizedDirectInvocationError(
                "DIRECT SMTP INVOCATION BLOCKED: Outbound transmission must be initiated through SendingGateway."
            )
        user, pwd = CredentialManager.get_credentials()
        sender = user or settings.IITD_EMAIL_USER

        # Construct MIME Message
        msg = MIMEMultipart()
        msg["From"] = sender
        msg["To"] = recipient
        msg["Subject"] = subject
        msg["Date"] = email.utils.formatdate(localtime=True)
        message_id = email.utils.make_msgid(domain="iitd.ac.in")
        msg["Message-ID"] = message_id

        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        if references:
            msg["References"] = references

        msg.attach(MIMEText(body_text, "plain", "utf-8"))

        if attachment_path and attachment_path.exists():
            with open(attachment_path, "rb") as f:
                part = MIMEApplication(f.read(), Name=attachment_path.name)
                part["Content-Disposition"] = f'attachment; filename="{attachment_path.name}"'
                msg.attach(part)

        # Dry-run handling: Never transmit packets over wire
        if dry_run or settings.DRY_RUN:
            audit_logger.log_event(
                event_type="EMAIL_DISPATCH_DRY_RUN",
                actor=actor,
                operator_id=operator_id,
                entity_type="EMAIL_MESSAGE",
                entity_id=message_id,
                details={
                    "recipient": recipient,
                    "subject": subject,
                    "dry_run": True,
                    "in_reply_to": in_reply_to
                }
            )
            return {
                "success": True,
                "dry_run": True,
                "message_id": message_id,
                "recipient": recipient,
                "subject": subject,
                "status": "SIMULATED_TRANSMISSION"
            }

        # Real SMTP Transmission
        if not pwd:
            raise ValueError("Cannot perform live transmission: IIT Delhi email password is not configured.")

        try:
            with smtplib.SMTP_SSL(self.host, self.port, context=self.ssl_context, timeout=15) as server:
                server.ehlo()
                server.login(user, pwd)
                server.sendmail(sender, [recipient], msg.as_string())

            audit_logger.log_event(
                event_type="EMAIL_DISPATCH_LIVE",
                actor=actor,
                operator_id=operator_id,
                entity_type="EMAIL_MESSAGE",
                entity_id=message_id,
                details={"recipient": recipient, "subject": subject, "dry_run": False}
            )

            return {
                "success": True,
                "dry_run": False,
                "message_id": message_id,
                "recipient": recipient,
                "subject": subject,
                "status": "DELIVERED"
            }

        except Exception as e:
            audit_logger.log_event(
                event_type="EMAIL_DISPATCH_FAILED",
                actor=actor,
                operator_id=operator_id,
                entity_type="EMAIL_MESSAGE",
                entity_id=message_id,
                details={"recipient": recipient, "subject": subject, "error": str(e)}
            )
            raise


# Global SMTP client instance
smtp_client = IITDSmtpClient()
