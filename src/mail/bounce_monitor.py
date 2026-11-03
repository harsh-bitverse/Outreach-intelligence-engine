"""
Ongoing Automatic IMAP Bounce Monitoring Module.
Runs lightweight incremental checks against IIT Delhi IMAP mailstore.
Maintains durable folder/UID checkpoints, safely detects UIDVALIDITY changes,
operates in strictly read-only mode, and updates SQLite and Excel immediately
when new delivery failures are discovered.
"""

import email
import imaplib
import logging
import ssl
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config.settings import settings
from src.database.connection import get_db
from src.mail.bounce_reconciler import (
    BounceReconciler,
    DeliveryStatusNotification,
    DSNParser,
)
from src.mail.credentials import CredentialManager
from src.security.audit_logger import audit_logger

logger = logging.getLogger(__name__)


class IMAPBounceMonitor:
    """
    Lightweight, durable IMAP bounce monitor.
    Periodically polls IMAP for new DSNs using UID checkpoints.
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        workbook_path: Optional[Path] = None,
        imap_host: Optional[str] = None,
        imap_port: Optional[int] = None,
        folder: str = "INBOX",
    ):
        self.db_path = Path(db_path or settings.SQLITE_DB_PATH)
        self.workbook_path = Path(workbook_path or settings.MASTER_WORKBOOK_PATH)
        self.imap_host = imap_host or settings.IITD_IMAP_SERVER
        self.imap_port = imap_port or settings.IITD_IMAP_PORT
        self.folder = folder
        self.reconciler = BounceReconciler(
            db_path=self.db_path,
            workbook_path=self.workbook_path,
            imap_host=self.imap_host,
            imap_port=self.imap_port,
        )

    def get_checkpoint(self) -> Tuple[Optional[int], int]:
        """
        Retrieves durable (uidvalidity, last_seen_uid) for the monitored folder from SQLite.
        """
        self.reconciler.init_database_tables()
        with get_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT uidvalidity, last_seen_uid FROM bounce_monitor_checkpoints WHERE folder = ?;",
                (self.folder,),
            ).fetchone()
            if row:
                return (row["uidvalidity"], row["last_seen_uid"])
            return (None, 0)

    def save_checkpoint(self, uidvalidity: int, last_seen_uid: int) -> None:
        """
        Persists updated (uidvalidity, last_seen_uid) checkpoint in SQLite.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        with get_db(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO bounce_monitor_checkpoints (
                    folder, uidvalidity, last_seen_uid, updated_at
                ) VALUES (?, ?, ?, ?);
            """, (self.folder, uidvalidity, last_seen_uid, now_iso))
            conn.commit()

    def check_for_new_bounces(self) -> Dict[str, Any]:
        """
        Poll IMAP for new messages since the last UID checkpoint.
        Strictly READ-ONLY (RFC 3501 EXAMINE) and uses BODY.PEEK to never touch message flags.
        Handles UIDVALIDITY resets safely.
        """
        user, pwd = CredentialManager.get_credentials()
        if not user or not pwd:
            return {"status": "NO_CREDENTIALS", "new_dsns": 0}

        saved_validity, last_seen_uid = self.get_checkpoint()

        client = imaplib.IMAP4_SSL(
            self.imap_host, self.imap_port, ssl_context=ssl.create_default_context()
        )
        auth_user = user
        try:
            client.login(auth_user, pwd)
        except imaplib.IMAP4.error as err:
            alt_user = (
                auth_user.split("@")[0]
                if "@" in auth_user
                else f"{auth_user}@iitd.ac.in"
            )
            client.login(alt_user, pwd)
            auth_user = alt_user

        # Select with readonly=True sends EXAMINE
        st, count_data = client.select(self.folder, readonly=True)
        if st != "OK":
            client.logout()
            return {"status": "SELECT_FAILED", "new_dsns": 0}

        # Check UIDVALIDITY
        current_validity = None
        for item in client.response("UIDVALIDITY")[1]:
            if item:
                try:
                    current_validity = int(item)
                except Exception:
                    pass

        # If UIDVALIDITY changed, previous UIDs are invalid: reset checkpoint
        if saved_validity is not None and current_validity is not None and saved_validity != current_validity:
            logger.warning(
                f"UIDVALIDITY changed from {saved_validity} to {current_validity}. Resetting UID checkpoint."
            )
            last_seen_uid = 0

        # Query UIDs greater than last_seen_uid
        # IMAP UID search syntax: UID <min>:*
        query = f"UID {last_seen_uid + 1}:*" if last_seen_uid > 0 else "ALL"
        st, search_data = client.uid("search", None, query)
        if st != "OK" or not search_data or not search_data[0]:
            client.logout()
            if current_validity:
                self.save_checkpoint(current_validity, last_seen_uid)
            return {"status": "NO_NEW_MESSAGES", "new_dsns": 0}

        candidate_uids = [
            int(u) for u in search_data[0].split() if int(u) > last_seen_uid
        ]

        if not candidate_uids:
            client.logout()
            return {"status": "UP_TO_DATE", "new_dsns": 0}

        new_dsns: List[DeliveryStatusNotification] = []
        max_uid_seen = last_seen_uid

        for u in candidate_uids:
            if u > max_uid_seen:
                max_uid_seen = u

            # Fetch body without setting \\Seen flag
            fst, fdata = client.uid("fetch", str(u).encode(), "(BODY.PEEK[])")
            if fst == "OK" and fdata and isinstance(fdata[0], tuple):
                raw_bytes = fdata[0][1]
                dsn = DSNParser.parse_message(folder=self.folder, uid=u, raw_bytes=raw_bytes)
                if dsn and self.reconciler._is_campaign_dsn(dsn):
                    new_dsns.append(dsn)

        client.logout()

        # Reconcile any new DSNs immediately
        reconcile_result = {}
        if new_dsns:
            reconcile_result = self.reconciler.reconcile_notifications(new_dsns)

        # Update durable checkpoint
        if current_validity:
            self.save_checkpoint(current_validity, max_uid_seen)

        audit_logger.log_event(
            event_type="BOUNCE_MONITOR_CHECK",
            actor="SYSTEM_SCHEDULER",
            entity_type="IMAP_MONITOR",
            entity_id=self.folder,
            details={
                "checked_uids": len(candidate_uids),
                "new_dsns": len(new_dsns),
                "last_seen_uid": max_uid_seen,
            },
        )

        return {
            "status": "CHECKED",
            "messages_checked": len(candidate_uids),
            "new_dsns": len(new_dsns),
            "last_seen_uid": max_uid_seen,
            "reconciliation": reconcile_result,
        }
