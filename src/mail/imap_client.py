"""
IIT Delhi IMAP Client Module.
Handles secure, authenticated connection to mailstore.iitd.ac.in on port 993 with SSL/TLS.
Supports read-only mailbox inspection, sent-folder reconciliation, and mock offline testing.
"""

import email
import imaplib
import ssl
from typing import Dict, List, Optional, Tuple, Any
from config.settings import settings
from src.mail.credentials import CredentialManager


class IITDImapClient:
    """Manages IMAP synchronization with IIT Delhi mailstore."""

    def __init__(self, host: Optional[str] = None, port: Optional[int] = None):
        self.host = host or settings.IITD_IMAP_SERVER
        self.port = port or settings.IITD_IMAP_PORT
        self.ssl_context = ssl.create_default_context()

    def test_connection(self) -> Dict[str, Any]:
        """
        Diagnostic connection check to IIT Delhi IMAP server.
        Validates TLS certificate, port connectivity, and authentication if credentials supplied.
        """
        user, pwd = CredentialManager.get_credentials()
        result = {
            "endpoint": f"{self.host}:{self.port}",
            "tls_established": False,
            "authenticated": False,
            "mailboxes": [],
            "error": None
        }

        try:
            client = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=self.ssl_context)
            result["tls_established"] = True

            if user and pwd:
                client.login(user, pwd)
                result["authenticated"] = True
                status, mailbox_list = client.list()
                if status == "OK" and mailbox_list:
                    result["mailboxes"] = [m.decode("utf-8", errors="ignore") for m in mailbox_list[:10]]
                client.logout()
            else:
                result["error"] = "TLS handshake successful. Credentials not supplied for authentication check."
                client.logout()

        except Exception as e:
            result["error"] = str(e)

        return result

    def inspect_inbox_readonly(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Read-only inspection of INBOX using IMAP EXAMINE (prevents altering unread flags).
        """
        user, pwd = CredentialManager.get_credentials()
        if not user or not pwd:
            return []

        messages = []
        try:
            client = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=self.ssl_context)
            client.login(user, pwd)

            # EXAMINE opens mailbox in strictly READ-ONLY mode
            status, _ = client.examine("INBOX")
            if status != "OK":
                client.logout()
                return []

            status, msg_nums = client.search(None, "ALL")
            if status == "OK" and msg_nums and msg_nums[0]:
                ids = msg_nums[0].split()
                # Take the most recent messages
                for num in reversed(ids[-limit:]):
                    status, data = client.fetch(num, "(RFC822.HEADER)")
                    if status == "OK" and data and isinstance(data[0], tuple):
                        raw_email = data[0][1]
                        msg = email.message_from_bytes(raw_email)
                        messages.append({
                            "message_id": msg.get("Message-ID", ""),
                            "subject": msg.get("Subject", ""),
                            "from": msg.get("From", ""),
                            "date": msg.get("Date", ""),
                            "in_reply_to": msg.get("In-Reply-To", ""),
                            "references": msg.get("References", "")
                        })

            client.logout()
        except Exception:
            pass

        return messages

    def reconcile_sent_folder(self) -> List[Dict[str, str]]:
        """
        Inspect Sent folder to identify previously contacted recipients.
        Never invents send confirmations.
        """
        user, pwd = CredentialManager.get_credentials()
        if not user or not pwd:
            return []

        contacted_recipients = []
        try:
            client = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=self.ssl_context)
            client.login(user, pwd)

            # Find sent mailbox name
            sent_boxes = ["Sent", "Sent Items", "INBOX.Sent"]
            opened_box = None
            for box in sent_boxes:
                status, _ = client.examine(box)
                if status == "OK":
                    opened_box = box
                    break

            if opened_box:
                status, msg_nums = client.search(None, "ALL")
                if status == "OK" and msg_nums and msg_nums[0]:
                    ids = msg_nums[0].split()
                    for num in reversed(ids[-50:]):
                        status, data = client.fetch(num, "(BODY[HEADER.FIELDS (TO MESSAGE-ID SUBJECT DATE)])")
                        if status == "OK" and data and isinstance(data[0], tuple):
                            msg = email.message_from_bytes(data[0][1])
                            contacted_recipients.append({
                                "to": msg.get("To", ""),
                                "subject": msg.get("Subject", ""),
                                "message_id": msg.get("Message-ID", ""),
                                "date": msg.get("Date", "")
                            })

            client.logout()
        except Exception:
            pass

        return contacted_recipients


# Global IMAP client instance
imap_client = IITDImapClient()
