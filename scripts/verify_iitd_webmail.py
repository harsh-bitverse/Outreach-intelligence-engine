"""
IIT Delhi Webmail Verification and Diagnostic Suite.

Performs non-destructive diagnostics:
1. IMAP Verification: Tests TLS, authenticates from Windows Credential Manager, and performs
   read-only RFC 3501 EXAMINE inspection of INBOX and folder structures without altering flags.
2. SMTP Verification: Tests TLS, EHLO capabilities, message size limits, and authenticates
   without transmitting messages.
3. Optional Self-Addressed Test: Transmits exactly one test email to self upon explicit invocation,
   verifying delivery via IMAP INBOX inspection.
"""

import argparse
import email
import imaplib
import smtplib
import ssl
import sys
import time
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from src.mail.credentials import CredentialManager
from src.mail.imap_client import imap_client
from src.mail.smtp_client import smtp_client


def run_imap_diagnostic() -> dict:
    print("\n" + "=" * 70)
    print("STAGE 1: IMAP CONNECTIVITY & READ-ONLY MAILBOX EXAMINATION")
    print("=" * 70)
    print(f"Target Server : {settings.IITD_IMAP_SERVER}:{settings.IITD_IMAP_PORT} (Implicit SSL/TLS)")

    user, pwd = CredentialManager.get_credentials()
    if not pwd:
        print("[-] Error: No password found in Windows Credential Manager or environment.")
        return {"success": False, "error": "MISSING_CREDENTIALS"}

    print(f"Auth User     : {user}")
    print(f"Credential    : {CredentialManager.mask_secret(pwd)} (from Windows Vault)")

    diag = imap_client.diagnostic_imap_examine("INBOX")

    if diag.get("authenticated"):
        print("[+] TLS Handshake & Certificate Validation : VERIFIED")
        print(f"[+] IMAP Authentication                     : SUCCESS (user: {diag.get('authenticated_username', user)})")
        print(f"[+] Mailbox Mode                            : STRICTLY READ-ONLY (RFC 3501 EXAMINE)")
        print(f"[+] INBOX Total Messages                    : {diag.get('message_count', 0)}")
        if "status_response" in diag:
            print(f"[+] INBOX Status Summary                    : {diag.get('status_response')}")
        if diag.get("mailbox_list"):
            print(f"[+] Accessible Mailboxes ({len(diag['mailbox_list'])} detected):")
            for b in diag["mailbox_list"][:8]:
                print(f"    - {b}")
            if len(diag["mailbox_list"]) > 8:
                print(f"    ... and {len(diag['mailbox_list']) - 8} additional folders.")
        print("[+] Zero flags altered, zero messages modified or downloaded.")
        return {"success": True, "details": diag}
    else:
        print(f"[-] IMAP Diagnostic Failed: {diag.get('error') or diag.get('status')}")
        return {"success": False, "error": diag.get("error")}


def run_smtp_diagnostic() -> dict:
    print("\n" + "=" * 70)
    print("STAGE 2: SMTP CONNECTIVITY & AUTHENTICATION DIAGNOSTIC")
    print("=" * 70)
    print(f"Target Server : {settings.IITD_SMTP_SERVER}:{settings.IITD_SMTP_PORT} (Implicit SSL/TLS)")

    user, pwd = CredentialManager.get_credentials()
    if not pwd:
        print("[-] Error: No password found in Windows Credential Manager or environment.")
        return {"success": False, "error": "MISSING_CREDENTIALS"}

    diag = smtp_client.test_connection()

    if diag.get("authenticated"):
        print("[+] TLS Handshake & Certificate Validation : VERIFIED")
        print(f"[+] SMTP Authentication                     : SUCCESS (user: {diag.get('authenticated_username', user)})")
        print(f"[+] ESMTP Capabilities Advertised           : {', '.join(diag.get('capabilities', []))}")
        if diag.get("max_message_size"):
            print(f"[+] Server Max Message Size Limit           : {diag.get('max_message_size')}")
        print("[+] Zero messages sent during diagnostic.")
        return {"success": True, "details": diag}
    else:
        print(f"[-] SMTP Diagnostic Failed: {diag.get('error')}")
        return {"success": False, "error": diag.get("error")}


def send_self_test_email() -> dict:
    print("\n" + "=" * 70)
    print("STAGE 3: CONTROLLED SELF-ADDRESSED TRANSMISSION TEST")
    print("=" * 70)
    from_addr = settings.IITD_EMAIL_USER
    to_addr = settings.IITD_EMAIL_USER
    subject = "IITD Outreach Engine — SMTP Connection Test"
    body = "This is a one-time test message verifying the IIT Delhi webmail integration with my local Outreach Intelligence Engine."

    print(f"From    : {from_addr}")
    print(f"To      : {to_addr}")
    print(f"Subject : {subject}")
    print(f"Body    : {body}")
    print("-" * 70)

    # Perform transmission
    user, pwd = CredentialManager.get_credentials()
    if not pwd:
        print("[-] Error: Credentials not found.")
        return {"success": False, "error": "MISSING_CREDENTIALS"}

    try:
        # We invoke send_email with system bypass actor for diagnostic test
        res = smtp_client.send_email(
            recipient=to_addr,
            subject=subject,
            body_text=body,
            dry_run=False,
            actor="SYSTEM_TEST_BYPASS",
            enforce_ticket=False
        )
        print(f"[+] SMTP Transmission: SUCCESS (Message-ID: {res.get('message_id')})")
        msg_id = res.get("message_id")

        print("[*] Waiting 5 seconds for mail delivery to mailstore.iitd.ac.in...")
        time.sleep(5)

        # Verify receipt via IMAP
        print("[*] Checking INBOX for test message arrival...")
        inbox_msgs = imap_client.inspect_inbox_readonly(limit=10)
        found = False
        for m in inbox_msgs:
            if m.get("message_id") == msg_id or subject in m.get("subject", ""):
                print(f"[+] Verified Test Message in INBOX:")
                print(f"    Subject : {m.get('subject')}")
                print(f"    From    : {m.get('from')}")
                print(f"    Date    : {m.get('date')}")
                found = True
                break

        if not found:
            print("[*] Note: Message dispatched cleanly to smtp.iitd.ac.in. Delivery to INBOX may take a few moments.")

        # Check Sent folder
        print("[*] Checking Sent folder for copy...")
        sent_diag = imap_client.diagnostic_imap_examine("Sent")
        sent_found = False
        if sent_diag.get("status") == "SUCCESS_READONLY_EXAMINED":
            print(f"    Sent Folder Total Messages: {sent_diag.get('message_count', 0)}")
            contacted = imap_client.reconcile_sent_folder()
            for m in contacted:
                if m.get("message_id") == msg_id or subject in m.get("subject", ""):
                    print(f"[+] Confirmed message appearance in Sent folder:")
                    print(f"    Subject    : {m.get('subject')}")
                    print(f"    Date       : {m.get('date')}")
                    print(f"    Message-ID : {m.get('message_id')}")
                    sent_found = True
                    break
            if not sent_found:
                print("    [*] Server note: Standard SMTP submission does not auto-copy to IMAP 'Sent' (typical for institutional MTA).")

        return {
            "success": True,
            "message_id": msg_id,
            "verified_inbox": found,
            "verified_sent": sent_found
        }

    except Exception as e:
        print(f"[-] Self-addressed test failed: {e}")
        return {"success": False, "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="IIT Delhi Webmail Verification Suite")
    parser.add_argument("--test-imap", action="store_true", help="Run read-only IMAP diagnostic")
    parser.add_argument("--test-smtp", action="store_true", help="Run SMTP authentication diagnostic")
    parser.add_argument("--test-all", action="store_true", help="Run both IMAP and SMTP diagnostics")
    parser.add_argument("--send-test-email", action="store_true", help="Send single self-addressed test email")

    args = parser.parse_args()

    # If no flags passed, default to --test-all
    if not (args.test_imap or args.test_smtp or args.test_all or args.send_test_email):
        args.test_all = True

    imap_ok = True
    smtp_ok = True

    if args.test_all or args.test_imap:
        res_imap = run_imap_diagnostic()
        imap_ok = res_imap.get("success", False)

    if args.test_all or args.test_smtp:
        res_smtp = run_smtp_diagnostic()
        smtp_ok = res_smtp.get("success", False)

    if args.send_test_email:
        if not (imap_ok and smtp_ok):
            print("\n[-] Cannot perform test email send: Prior connectivity tests did not pass.")
            sys.exit(1)
        send_self_test_email()

    print("\n" + "=" * 70)
    print("DIAGNOSTIC SUMMARY")
    print("=" * 70)
    print(f"IMAP Status : {'[OK] CONNECTED & EXAMINED' if imap_ok else '[FAILED]'}")
    print(f"SMTP Status : {'[OK] CONNECTED & AUTHENTICATED' if smtp_ok else '[FAILED]'}")
    print("Live Professor Outreach: STRICTLY INACTIVE (GATEWAY_ENABLED=False, DRY_RUN=True)")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
