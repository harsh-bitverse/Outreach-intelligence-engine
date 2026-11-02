"""
Sync Sent Emails to IIT Delhi IMAP Sent Folder.
Appends the 37 legitimately transmitted emails to mailstore.iitd.ac.in 'Sent' mailbox
so they appear natively in IITD Webmail interface.
"""

import email.utils
import imaplib
import sqlite3
import sys
import time
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from src.mail.credentials import CredentialManager


def sync_sent_emails():
    print("=" * 80)
    print("  SYNCHRONIZING SENT OUTREACH MESSAGES TO IITD IMAP SENT FOLDER")
    print("=" * 80)

    user, pwd = CredentialManager.get_credentials()
    if not user or not pwd:
        print("[-] Error: Credentials not found in keyring.")
        return

    # 1. Fetch 37 SENT drafts from SQLite
    db_path = settings.SQLITE_DB_PATH
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        SELECT d.recipient_email, d.subject, d.body_text, d.dispatched_at, p.full_name, p.notes
        FROM outreach_drafts d
        JOIN professors p ON d.professor_id = p.professor_id
        WHERE d.status = 'SENT'
        ORDER BY d.dispatched_at ASC;
    """)
    rows = cur.fetchall()
    conn.close()

    print(f"[*] Found {len(rows)} SENT drafts in SQLite database.")
    if len(rows) == 0:
        print("[-] No SENT drafts found to sync.")
        return

    # Check attachments
    cv_p = settings.CV_ATTACHMENT_PATH
    ri_p = settings.RESEARCH_INTERESTS_PATH
    if not cv_p.exists() or not ri_p.exists():
        print("[-] Error: Attachments missing.")
        return

    with open(cv_p, "rb") as f:
        cv_bytes = f.read()
    with open(ri_p, "rb") as f:
        ri_bytes = f.read()

    # 2. Connect to IMAP
    client = imaplib.IMAP4_SSL("mailstore.iitd.ac.in", 993)
    client.login(user, pwd)
    st, d = client.select('"Sent"')
    initial_count = int(d[0].decode())
    print(f"[*] Current message count in 'Sent' mailbox: {initial_count}")

    synced_count = 0
    for idx, (rcpt, subj, body, dispatched_at_str, full_name, notes) in enumerate(rows, start=1):
        # Extract message-id from notes (e.g., 'SENT:<...>')
        msg_id = None
        if notes and "SENT:<" in notes:
            start_i = notes.find("SENT:<") + 5
            end_i = notes.find(">", start_i)
            if end_i != -1:
                msg_id = notes[start_i:end_i + 1]

        if not msg_id:
            msg_id = email.utils.make_msgid(domain="iitd.ac.in")

        # Parse timestamp
        try:
            dt = datetime.fromisoformat(dispatched_at_str)
            timestamp = dt.timestamp()
            date_header = email.utils.formatdate(timestamp, localtime=True)
        except Exception:
            timestamp = time.time()
            date_header = email.utils.formatdate(timestamp, localtime=True)

        # Build MIME message
        msg = MIMEMultipart()
        msg["From"] = user
        msg["To"] = rcpt
        msg["Subject"] = subj
        msg["Date"] = date_header
        msg["Message-ID"] = msg_id

        msg.attach(MIMEText(body, "plain", "utf-8"))

        part_cv = MIMEApplication(cv_bytes, Name=settings.CV_ATTACHMENT_FILENAME)
        part_cv["Content-Disposition"] = f'attachment; filename="{settings.CV_ATTACHMENT_FILENAME}"'
        msg.attach(part_cv)

        part_ri = MIMEApplication(ri_bytes, Name=settings.RESEARCH_INTERESTS_FILENAME)
        part_ri["Content-Disposition"] = f'attachment; filename="{settings.RESEARCH_INTERESTS_FILENAME}"'
        msg.attach(part_ri)

        # Append to IMAP Sent
        date_param = imaplib.Time2Internaldate(timestamp)
        st_app, resp = client.append('"Sent"', "\\Seen", date_param, msg.as_bytes())
        if st_app == "OK":
            synced_count += 1
            print(f"  [{idx:02d}/37] Appended to Sent: {rcpt} | {subj[:45]}...")
        else:
            print(f"  [{idx:02d}/37] Failed to append: {rcpt} - {resp}")

    st, d = client.select('"Sent"')
    final_count = int(d[0].decode())
    print("-" * 80)
    print(f"[+] Successfully synced {synced_count}/37 messages to IMAP Sent folder.")
    print(f"[+] Updated Sent mailbox count: {initial_count} -> {final_count}")
    client.logout()


if __name__ == "__main__":
    sync_sent_emails()
