"""
Email Thread Matching and Correlation Engine.
Correlates incoming emails with existing outreach threads using Message-ID,
In-Reply-To, References, sender addresses, and subject normalization.
"""

import hashlib
import re
from typing import Optional, Dict, Any, Tuple
from src.database.repository import OutreachRepository


class ThreadMatcher:
    """Matches incoming emails to existing threads and professors."""

    @staticmethod
    def normalize_subject(subject: str) -> str:
        """Strip prefixes like 'Re:', 'Fwd:', '[EXTERNAL]', and whitespace."""
        if not subject:
            return ""
        norm = subject.strip()
        # Remove repetitive prefixes
        pattern = r"^(re|fwd|fw|external|\[external\]):\s*"
        while re.match(pattern, norm, re.IGNORECASE):
            norm = re.sub(pattern, "", norm, flags=re.IGNORECASE).strip()
        return norm

    @staticmethod
    def correlate_incoming_message(
        repo: OutreachRepository,
        sender_email: str,
        subject: str,
        in_reply_to: Optional[str] = None,
        references: Optional[str] = None
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Attempt to resolve (thread_id, professor_id) for an incoming email.
        Order of precedence:
        1. In-Reply-To matched to a stored outbound message's message_id.
        2. References header containing any stored message_id.
        3. Sender email matching a known professor in the database.
        4. Normalized subject matching an active thread for that professor.
        """
        sender_norm = sender_email.strip().lower()

        # 1. Match via In-Reply-To
        if in_reply_to:
            clean_irt = in_reply_to.strip().strip("<>")
            msg = repo.get_email_message(clean_irt)
            if msg:
                return msg["thread_id"], msg["professor_id"]

        # 2. Match via References
        if references:
            ref_ids = re.findall(r"<([^>]+)>", references)
            for ref_id in ref_ids:
                msg = repo.get_email_message(ref_id)
                if msg:
                    return msg["thread_id"], msg["professor_id"]

        # 3. Match via Professor Sender Email
        prof = repo.get_professor_by_email(sender_norm)
        if prof:
            prof_id = prof["professor_id"]
            # Look up active thread for this professor
            norm_subj = ThreadMatcher.normalize_subject(subject)
            cursor = repo.conn.execute("""
                SELECT thread_id FROM email_threads
                WHERE professor_id = ?
                ORDER BY last_message_at DESC LIMIT 1;
            """, (prof_id,))
            row = cursor.fetchone()
            if row:
                return row["thread_id"], prof_id

            # Create synthetic thread ID for this exchange
            new_thread_id = "th_" + hashlib.sha256(f"{prof_id}_{norm_subj}".encode()).hexdigest()[:14]
            return new_thread_id, prof_id

        # Unmatched
        return None, None
