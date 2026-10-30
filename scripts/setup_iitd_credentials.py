"""
IIT Delhi Webmail Interactive Credential Configuration Utility.

Prompts the operator securely for their IIT Delhi Kerberos password using masked input.
Verifies credentials live against mailstore.iitd.ac.in (IMAP:993) and smtp.iitd.ac.in (SMTP:465)
and deposits the verified secret into the Windows Credential Manager using Python keyring.

Zero plaintext storage: Password is never written to .env, disk files, terminal history, or logs.
"""

import getpass
import imaplib
import os
import smtplib
import ssl
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from src.mail.credentials import CredentialManager


def main():
    print("=" * 76)
    print(" IIT DELHI WEBMAIL — INTERACTIVE CREDENTIAL SETUP & SECURE STORAGE")
    print("=" * 76)
    print(f" Institutional Email : {settings.IITD_EMAIL_USER}")
    print(f" Default Kerberos ID : ch1251635")
    print(f" IMAP Server Target  : {settings.IITD_IMAP_SERVER}:{settings.IITD_IMAP_PORT} (Implicit SSL/TLS)")
    print(f" SMTP Server Target  : {settings.IITD_SMTP_SERVER}:{settings.IITD_SMTP_PORT} (Implicit SSL/TLS)")
    print(f" Storage Target      : Windows Credential Manager (Service: {CredentialManager.SERVICE_NAME})")
    print("=" * 76)
    print(" SECURITY NOTICE:")
    print(" * Your password is NEVER stored in .env, source code, git, or log files.")
    print(" * Input will be completely masked (no characters will be echoed on screen).")
    print(" * Credentials are saved directly into the encrypted Windows Credential Manager.")
    print("=" * 76)
    print()

    # Step 1: Secure masked input
    try:
        pwd1 = getpass.getpass("Enter IIT Delhi Kerberos Password: ")
    except (KeyboardInterrupt, EOFError):
        print("\n[!] Setup cancelled by operator.")
        sys.exit(1)

    if not pwd1 or len(pwd1.strip()) == 0:
        print("\n[!] Error: Password cannot be empty.")
        sys.exit(1)

    try:
        pwd2 = getpass.getpass("Re-enter Password to Confirm:     ")
    except (KeyboardInterrupt, EOFError):
        print("\n[!] Setup cancelled by operator.")
        sys.exit(1)

    if pwd1 != pwd2:
        print("\n[!] Error: Passwords did not match. Please run the script again.")
        sys.exit(1)

    password = pwd1.strip()
    print("\n[1/3] Testing live IMAP connection & authentication...")
    ssl_context = ssl.create_default_context()
    imap_user_used = None

    try:
        imap_client = imaplib.IMAP4_SSL(settings.IITD_IMAP_SERVER, settings.IITD_IMAP_PORT, ssl_context=ssl_context)
        print("  [+] TLS handshake with mailstore.iitd.ac.in:993 successful.")

        # Test Kerberos ID first, then full email if needed
        candidate_users = ["ch1251635", settings.IITD_EMAIL_USER]
        auth_success = False
        last_imap_err = None

        for u in candidate_users:
            try:
                imap_client.login(u, password)
                imap_user_used = u
                auth_success = True
                break
            except imaplib.IMAP4.error as err:
                last_imap_err = err

        if not auth_success:
            print(f"  [-] IMAP Authentication failed: {last_imap_err}")
            print("\n[!] Diagnostic Guidance:")
            print("  - Ensure your Kerberos password is correct (case-sensitive).")
            print("  - If you are off-campus, verify if IITD CSC requires Fortinet SSL VPN active.")
            sys.exit(1)

        print(f"  [+] IMAP Authentication SUCCESSFUL (Authenticated as: {imap_user_used})")
        imap_client.logout()

    except Exception as e:
        print(f"  [-] Connection error to IMAP server: {e}")
        sys.exit(1)

    print("\n[2/3] Testing live SMTP connection & authentication...")
    smtp_user_used = None
    try:
        with smtplib.SMTP_SSL(settings.IITD_SMTP_SERVER, settings.IITD_SMTP_PORT, context=ssl_context, timeout=10) as s:
            print("  [+] TLS handshake with smtp.iitd.ac.in:465 successful.")
            s.ehlo()

            # Inspect features
            if hasattr(s, "esmtp_features") and s.esmtp_features:
                caps = sorted(list(s.esmtp_features.keys()))
                print(f"  [+] Server ESMTP Capabilities: {', '.join(caps)}")
                if "size" in s.esmtp_features:
                    size_b = s.esmtp_features["size"]
                    try:
                        mb = int(size_b) / (1024 * 1024)
                        print(f"  [+] Advertised Max Message Size: {mb:.1f} MB ({size_b} bytes)")
                    except Exception:
                        pass

            # Test candidate usernames
            candidate_smtp = [imap_user_used, "ch1251635", settings.IITD_EMAIL_USER]
            # Deduplicate preserving order
            seen = set()
            cand_list = [x for x in candidate_smtp if x and not (x in seen or seen.add(x))]

            smtp_auth_success = False
            last_smtp_err = None
            for u in cand_list:
                try:
                    s.login(u, password)
                    smtp_user_used = u
                    smtp_auth_success = True
                    break
                except smtplib.SMTPAuthenticationError as err:
                    last_smtp_err = err

            if not smtp_auth_success:
                print(f"  [-] SMTP Authentication failed: {last_smtp_err}")
                print("\n[!] Diagnostic Guidance:")
                print("  - Ensure SMTP outbound authentication is permitted for your Kerberos account.")
                sys.exit(1)

            print(f"  [+] SMTP Authentication SUCCESSFUL (Authenticated as: {smtp_user_used})")

    except Exception as e:
        print(f"  [-] Connection error to SMTP server: {e}")
        sys.exit(1)

    print("\n[3/3] Depositing verified credentials into Windows Credential Manager...")
    try:
        # Securely store in OS keyring under service name
        CredentialManager.set_credentials("ch1251635", password)
        CredentialManager.set_credentials(settings.IITD_EMAIL_USER, password)

        # Verify retrieval from keyring
        test_u, test_p = CredentialManager.get_credentials()
        if test_p == password:
            print("  [+] Verified secret retrieval from Windows Credential Manager.")
        else:
            print("  [!] Warning: Retrieved secret does not match entered password.")
            sys.exit(1)

    except Exception as e:
        print(f"  [-] Failed to write to Windows Credential Manager: {e}")
        sys.exit(1)

    print()
    print("=" * 76)
    print(" [SUCCESS] IIT DELHI WEBMAIL CREDENTIAL CONFIGURATION COMPLETE")
    print("=" * 76)
    print(f" Stored Service Name : {CredentialManager.SERVICE_NAME}")
    print(f" Kerberos Usernames  : ch1251635, {settings.IITD_EMAIL_USER}")
    print(f" IMAP Verified User  : {imap_user_used}")
    print(f" SMTP Verified User  : {smtp_user_used}")
    print(f" Storage Backend     : Windows Vault / Windows Credential Manager")
    print(f" Plaintext in .env   : ZERO (No credentials stored in configuration files)")
    print("=" * 76)
    print("\nYou may now notify Antigravity that credential setup succeeded.")


if __name__ == "__main__":
    main()
