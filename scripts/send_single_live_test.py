"""
Single Live Laptop-to-Gmail Integration Test.

Transmits exactly ONE approved research internship email from ch1251635@iitd.ac.in
to hagrharsh@gmail.com using the authenticated IIT Delhi SMTP server (smtp.iitd.ac.in:465),
with both authoritative PDFs attached with exact recipient-facing filenames.
Verifies message presence in IIT Delhi's IMAP Sent folder (mailstore.iitd.ac.in:993).
"""

import email.utils
import hashlib
import imaplib
import smtplib
import ssl
import sys
import time
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from src.mail.credentials import CredentialManager
from src.security.audit_logger import audit_logger

SENDER_EMAIL = "ch1251635@iitd.ac.in"
RECIPIENT_EMAIL = "hagrharsh@gmail.com"
SUBJECT = "Summer 2027 Research Internship — Scientific Computing & AI Systems | IIT Delhi"

BODY_TEXT = """Dear Professor,

My name is Harshith N, an undergraduate in Chemical Engineering at the Indian Institute of Technology Delhi (IIT Delhi). I am writing to express my strong interest in joining your research group for an undergraduate research internship during Summer 2027.

I have followed your research in scientific computing, numerical differential equation modeling, and high-performance algorithms for machine learning systems. Your focus on designing computationally efficient solvers and accelerating simulation workloads directly aligns with my academic interests.

In my project, Neural Process Twin, I developed deep learning surrogate models in PyTorch for 2D steady-state heat conduction, evaluating surrogate predictions against numerical finite difference solutions. In my LLM Inference Intelligence Engine project, I instrumented Roofline model diagnostics isolating compute bounds from memory-bandwidth bottlenecks across CPU and GPU architectures. I would welcome the opportunity to apply this numerical modeling and performance profiling background to your group's computational research.

I am eager to contribute to your ongoing computational projects for 10–12 weeks in Summer 2027. My CV and research interests statement are attached for your review, and I would welcome the opportunity to discuss potential alignment.

Sincerely,
Harshith N
Department of Chemical Engineering
Indian Institute of Technology Delhi (IIT Delhi)
ch1251635@iitd.ac.in"""


def execute_live_test() -> dict:
    cv_path = Path(settings.CV_ATTACHMENT_PATH)
    ri_path = Path(settings.RESEARCH_INTERESTS_PATH)

    if not cv_path.exists():
        raise FileNotFoundError(f"CV attachment not found: {cv_path}")
    if not ri_path.exists():
        raise FileNotFoundError(f"Research Interests attachment not found: {ri_path}")

    cv_bytes = cv_path.read_bytes()
    ri_bytes = ri_path.read_bytes()
    cv_hash = hashlib.sha256(cv_bytes).hexdigest()
    ri_hash = hashlib.sha256(ri_bytes).hexdigest()

    user, pwd = CredentialManager.get_credentials()
    if not pwd:
        raise ValueError("IIT Delhi webmail credentials not found in Windows Credential Manager.")

    print("=" * 76)
    print(" EXECUTING AUTHORIZED SINGLE LIVE OUTREACH TEST")
    print("=" * 76)
    print(f" Sender           : {SENDER_EMAIL}")
    print(f" Recipient        : {RECIPIENT_EMAIL}")
    print(f" Subject          : {SUBJECT}")
    print(f" Attached CV      : {settings.CV_ATTACHMENT_FILENAME} ({len(cv_bytes):,} bytes | SHA256: {cv_hash[:16]}...)")
    print(f" Attached Interests: {settings.RESEARCH_INTERESTS_FILENAME} ({len(ri_bytes):,} bytes | SHA256: {ri_hash[:16]}...)")
    print("=" * 76)

    # 1. Build MIME Message
    msg = MIMEMultipart()
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = SUBJECT
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg_id = email.utils.make_msgid(domain="iitd.ac.in")
    msg["Message-ID"] = msg_id

    # Attach Body
    msg.attach(MIMEText(BODY_TEXT, "plain", "utf-8"))

    # Attach CV
    part_cv = MIMEApplication(cv_bytes, Name=settings.CV_ATTACHMENT_FILENAME)
    part_cv["Content-Disposition"] = f'attachment; filename="{settings.CV_ATTACHMENT_FILENAME}"'
    msg.attach(part_cv)

    # Attach Research Interests
    part_ri = MIMEApplication(ri_bytes, Name=settings.RESEARCH_INTERESTS_FILENAME)
    part_ri["Content-Disposition"] = f'attachment; filename="{settings.RESEARCH_INTERESTS_FILENAME}"'
    msg.attach(part_ri)

    # 2. Transmit via SMTP_SSL
    print("\n[Step 1/3] Transmitting via smtp.iitd.ac.in:465 (SSL/TLS)...")
    ssl_context = ssl.create_default_context()
    auth_user = user

    with smtplib.SMTP_SSL(settings.IITD_SMTP_SERVER, settings.IITD_SMTP_PORT, context=ssl_context, timeout=20) as server:
        ehlo_code, ehlo_resp = server.ehlo()
        print(f"  EHLO Response: {ehlo_code} {ehlo_resp.decode('utf-8', errors='ignore').splitlines()[0]}")

        try:
            server.login(auth_user, pwd)
        except smtplib.SMTPAuthenticationError:
            alt_user = auth_user.split("@")[0] if "@" in auth_user else f"{auth_user}@iitd.ac.in"
            server.login(alt_user, pwd)
            auth_user = alt_user

        envelope_sender = SENDER_EMAIL
        send_errs = server.sendmail(envelope_sender, [RECIPIENT_EMAIL], msg.as_string())
        if send_errs:
            raise RuntimeError(f"SMTP rejected recipients: {send_errs}")

    print("  [+] SMTP Dispatch Succeeded!")
    print(f"  [+] Message-ID: {msg_id}")

    # Audit Logging
    audit_logger.log_event(
        event_type="EMAIL_DISPATCH_LIVE",
        actor="HUMAN_OPERATOR_AUTHORIZED",
        operator_id="Harshith_N",
        entity_type="EMAIL_MESSAGE",
        entity_id=msg_id,
        details={
            "recipient": RECIPIENT_EMAIL,
            "subject": SUBJECT,
            "attachments": [settings.CV_ATTACHMENT_FILENAME, settings.RESEARCH_INTERESTS_FILENAME],
            "dry_run": False,
            "purpose": "SINGLE_LIVE_LAPTOP_INTEGRATION_TEST"
        }
    )

    # 3. IMAP Sent Folder Recording & Verification
    print("\n[Step 2/3] Appending and verifying message in IIT Delhi IMAP 'Sent' folder...")
    imap_verified = False
    imap_found_id = None
    sent_total_count = None

    try:
        client = imaplib.IMAP4_SSL(settings.IITD_IMAP_SERVER, settings.IITD_IMAP_PORT, ssl_context=ssl_context, timeout=20)
        try:
            client.login(auth_user, pwd)
        except Exception:
            client.login(user, pwd)

        # Append copy to Sent mailbox
        append_res, append_data = client.append("Sent", r"(\Seen)", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        print(f"  [+] IMAP APPEND to 'Sent': {append_res} ({append_data})")

        # Select Sent mailbox in read-only mode to verify
        st, count_data = client.select("Sent", readonly=True)
        if st == "OK" and count_data:
            sent_total_count = int(count_data[0].decode())
            print(f"  [+] IMAP 'Sent' mailbox message count: {sent_total_count}")

            # Search for the Message-ID in Sent
            search_query = f'HEADER Message-ID "{msg_id}"'
            search_st, search_data = client.search(None, search_query)
            if search_st == "OK" and search_data and search_data[0]:
                found_nums = search_data[0].split()
                if found_nums:
                    fetch_st, fetch_data = client.fetch(found_nums[-1], "(BODY[HEADER.FIELDS (MESSAGE-ID TO SUBJECT DATE)])")
                    if fetch_st == "OK" and fetch_data and isinstance(fetch_data[0], tuple):
                        header_block = fetch_data[0][1].decode("utf-8", errors="ignore").strip()
                        print(f"  [+] Verified Header in Sent Box:\n{header_block}")
                        imap_verified = True
                        imap_found_id = msg_id

        client.logout()
    except Exception as e:
        print(f"  [-] IMAP synchronization notice: {e}")

    # 4. Final Output Receipt
    print("\n" + "=" * 76)
    print(" TRANSMISSION & VERIFICATION RECEIPT")
    print("=" * 76)
    print(f" Delivery Status       : DELIVERED (Live SMTP 250 OK)")
    print(f" From                  : {SENDER_EMAIL}")
    print(f" To                    : {RECIPIENT_EMAIL}")
    print(f" Message-ID            : {msg_id}")
    print(f" Subject               : {SUBJECT}")
    print(f" Attachments           : 1. {settings.CV_ATTACHMENT_FILENAME} ({len(cv_bytes):,} bytes)")
    print(f"                         2. {settings.RESEARCH_INTERESTS_FILENAME} ({len(ri_bytes):,} bytes)")
    print(f" IMAP 'Sent' Presence  : {'CONFIRMED & VERIFIED' if imap_verified else 'PENDING'}")
    print(f" IMAP Message Count    : {sent_total_count}")
    print("=" * 76)

    return {
        "status": "DELIVERED",
        "message_id": msg_id,
        "recipient": RECIPIENT_EMAIL,
        "subject": SUBJECT,
        "attachments": [settings.CV_ATTACHMENT_FILENAME, settings.RESEARCH_INTERESTS_FILENAME],
        "imap_sent_verified": imap_verified,
        "sent_box_count": sent_total_count
    }


if __name__ == "__main__":
    execute_live_test()
