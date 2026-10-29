"""
Database Repository Module.
Handles CRUD operations, durable ID generation, suppression checks, and queries.
"""

import hashlib
import json
import sqlite3
from typing import List, Optional, Dict, Any, Tuple
from src.database.models import (
    Professor, University, OutreachDraft, EmailThread, EmailMessage,
    ReplyClassificationRecord, ReplyDraftRecord, FollowUpDraftRecord, SuppressionRecord
)


def compute_durable_professor_id(university_name: str, full_name: str) -> str:
    """
    Generate a deterministic, immutable 16-character identifier
    based on normalized university and professor name.
    """
    norm = f"{university_name.strip().lower()}::{full_name.strip().lower()}"
    return "prof_" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


class OutreachRepository:
    """Data repository interface for Outreach Intelligence Engine."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # --------------------------------------------------------------------------
    # Suppression Management
    # --------------------------------------------------------------------------
    def is_suppressed(self, email: Optional[str]) -> Tuple[bool, Optional[str]]:
        """
        Check if an email address or its domain is on the suppression list.
        Returns (True, reason) if suppressed, else (False, None).
        """
        if not email or not email.strip():
            return False, None

        norm_email = email.strip().lower()
        domain = norm_email.split("@")[-1] if "@" in norm_email else ""

        cursor = self.conn.execute("""
            SELECT reason, notes FROM suppression_list
            WHERE email = ? OR domain = ?
            LIMIT 1;
        """, (norm_email, domain))
        row = cursor.fetchone()
        if row:
            return True, f"{row['reason']}: {row['notes']}"
        return False, None

    def add_suppression(
        self,
        email: Optional[str] = None,
        domain: Optional[str] = None,
        reason: str = "MANUAL",
        notes: str = ""
    ) -> str:
        """Add an email or domain to the suppression list."""
        norm_email = email.strip().lower() if email else None
        norm_domain = domain.strip().lower() if domain else None
        supp_id = "supp_" + hashlib.sha256(f"{norm_email}_{norm_domain}".encode()).hexdigest()[:12]

        self.conn.execute("""
            INSERT INTO suppression_list (suppression_id, email, domain, reason, notes, created_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(suppression_id) DO UPDATE SET
                reason = excluded.reason,
                notes = excluded.notes;
        """, (supp_id, norm_email, norm_domain, reason, notes))
        return supp_id

    def list_suppressions(self) -> List[Dict[str, Any]]:
        cursor = self.conn.execute("SELECT * FROM suppression_list ORDER BY created_at DESC;")
        return [dict(row) for row in cursor.fetchall()]

    # --------------------------------------------------------------------------
    # Professor Management
    # --------------------------------------------------------------------------
    def upsert_professor(self, prof: Professor) -> str:
        """
        Insert or update a professor record.
        Preserves existing outreach_status if already present.
        """
        # Check if already suppressed
        suppressed, reason = self.is_suppressed(prof.email)
        initial_status = "SUPPRESSED" if suppressed else prof.outreach_status

        self.conn.execute("""
            INSERT INTO professors (
                professor_id, qs_rank, processing_sequence, university_name, country,
                full_name, title, department, email, email_verification_status,
                verification_source_url, faculty_profile_url, research_areas,
                research_overlap, cv_skill_project, potential_contribution,
                evidence_url, date_verified, notes, outreach_status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(professor_id) DO UPDATE SET
                qs_rank = COALESCE(excluded.qs_rank, professors.qs_rank),
                processing_sequence = COALESCE(excluded.processing_sequence, professors.processing_sequence),
                title = excluded.title,
                department = excluded.department,
                email = excluded.email,
                email_verification_status = excluded.email_verification_status,
                verification_source_url = excluded.verification_source_url,
                faculty_profile_url = excluded.faculty_profile_url,
                research_areas = excluded.research_areas,
                research_overlap = excluded.research_overlap,
                cv_skill_project = excluded.cv_skill_project,
                potential_contribution = excluded.potential_contribution,
                evidence_url = excluded.evidence_url,
                date_verified = excluded.date_verified,
                notes = excluded.notes,
                outreach_status = CASE 
                    WHEN excluded.outreach_status = 'SUPPRESSED' THEN 'SUPPRESSED'
                    ELSE professors.outreach_status
                END,
                updated_at = datetime('now');
        """, (
            prof.professor_id, prof.qs_rank, prof.processing_sequence, prof.university_name, prof.country,
            prof.full_name, prof.title, prof.department, prof.email, prof.email_verification_status,
            prof.verification_source_url, prof.faculty_profile_url, prof.research_areas,
            prof.research_overlap, prof.cv_skill_project, prof.potential_contribution,
            prof.evidence_url, prof.date_verified, prof.notes, initial_status, prof.created_at
        ))
        return prof.professor_id

    def get_professor(self, professor_id: str) -> Optional[Dict[str, Any]]:
        cursor = self.conn.execute("SELECT * FROM professors WHERE professor_id = ?;", (professor_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_professor_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        if not email:
            return None
        cursor = self.conn.execute(
            "SELECT * FROM professors WHERE LOWER(email) = LOWER(?) LIMIT 1;",
            (email.strip(),)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def list_professors(
        self,
        university: Optional[str] = None,
        country: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM professors WHERE 1=1"
        params: List[Any] = []
        if university:
            query += " AND university_name LIKE ?"
            params.append(f"%{university}%")
        if country:
            query += " AND country LIKE ?"
            params.append(f"%{country}%")
        if status:
            query += " AND outreach_status = ?"
            params.append(status)

        query += " ORDER BY qs_rank ASC, processing_sequence ASC, full_name ASC LIMIT ? OFFSET ?;"
        params.extend([limit, offset])

        cursor = self.conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def count_professors(self, status: Optional[str] = None) -> int:
        if status:
            cursor = self.conn.execute("SELECT COUNT(*) FROM professors WHERE outreach_status = ?;", (status,))
        else:
            cursor = self.conn.execute("SELECT COUNT(*) FROM professors;")
        return cursor.fetchone()[0]

    def update_professor_status(self, professor_id: str, new_status: str, notes: Optional[str] = None) -> None:
        """Update outreach status for a professor."""
        if notes:
            self.conn.execute("""
                UPDATE professors 
                SET outreach_status = ?, notes = notes || ' | ' || ?, updated_at = datetime('now')
                WHERE professor_id = ?;
            """, (new_status, notes, professor_id))
        else:
            self.conn.execute("""
                UPDATE professors 
                SET outreach_status = ?, updated_at = datetime('now')
                WHERE professor_id = ?;
            """, (new_status, professor_id))

    # --------------------------------------------------------------------------
    # University Management
    # --------------------------------------------------------------------------
    def upsert_university(self, uni: University) -> None:
        self.conn.execute("""
            INSERT INTO universities (
                university_name, country, qs_rank, processing_sequence, city,
                official_url, review_status, departments_reviewed, directories_reviewed,
                profiles_screened, professors_shortlisted, verified_emails, unverified_emails,
                timezone_iana, last_updated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(university_name) DO UPDATE SET
                qs_rank = COALESCE(excluded.qs_rank, universities.qs_rank),
                processing_sequence = COALESCE(excluded.processing_sequence, universities.processing_sequence),
                city = excluded.city,
                official_url = excluded.official_url,
                review_status = excluded.review_status,
                departments_reviewed = excluded.departments_reviewed,
                directories_reviewed = excluded.directories_reviewed,
                profiles_screened = excluded.profiles_screened,
                professors_shortlisted = excluded.professors_shortlisted,
                verified_emails = excluded.verified_emails,
                unverified_emails = excluded.unverified_emails,
                timezone_iana = excluded.timezone_iana,
                last_updated = datetime('now');
        """, (
            uni.university_name, uni.country, uni.qs_rank, uni.processing_sequence, uni.city,
            uni.official_url, uni.review_status, uni.departments_reviewed, uni.directories_reviewed,
            uni.profiles_screened, uni.professors_shortlisted, uni.verified_emails, uni.unverified_emails,
            uni.timezone_iana
        ))

    def list_universities(self) -> List[Dict[str, Any]]:
        cursor = self.conn.execute("SELECT * FROM universities ORDER BY qs_rank ASC, processing_sequence ASC;")
        return [dict(row) for row in cursor.fetchall()]

    # --------------------------------------------------------------------------
    # Outreach Drafts
    # --------------------------------------------------------------------------
    def save_outreach_draft(self, draft: OutreachDraft) -> str:
        self.conn.execute("""
            INSERT INTO outreach_drafts (
                draft_id, professor_id, recipient_email, subject, body_text,
                word_count, status, created_at, approved_at, approved_by, dispatched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(draft_id) DO UPDATE SET
                subject = excluded.subject,
                body_text = excluded.body_text,
                word_count = excluded.word_count,
                status = excluded.status,
                approved_at = excluded.approved_at,
                approved_by = excluded.approved_by,
                dispatched_at = excluded.dispatched_at;
        """, (
            draft.draft_id, draft.professor_id, draft.recipient_email, draft.subject,
            draft.body_text, draft.word_count, draft.status, draft.created_at,
            draft.approved_at, draft.approved_by, draft.dispatched_at
        ))
        return draft.draft_id

    def get_outreach_draft_by_professor(self, professor_id: str) -> Optional[Dict[str, Any]]:
        cursor = self.conn.execute(
            "SELECT * FROM outreach_drafts WHERE professor_id = ? ORDER BY created_at DESC LIMIT 1;",
            (professor_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def list_outreach_drafts(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        if status:
            cursor = self.conn.execute(
                "SELECT * FROM outreach_drafts WHERE status = ? ORDER BY created_at DESC;",
                (status,)
            )
        else:
            cursor = self.conn.execute("SELECT * FROM outreach_drafts ORDER BY created_at DESC;")
        return [dict(row) for row in cursor.fetchall()]

    # --------------------------------------------------------------------------
    # Threads & Messages
    # --------------------------------------------------------------------------
    def get_or_create_thread(self, thread_id: str, professor_id: str, subject: str) -> str:
        self.conn.execute("""
            INSERT INTO email_threads (thread_id, professor_id, subject, status, message_count, last_message_at, created_at)
            VALUES (?, ?, ?, 'ACTIVE', 1, datetime('now'), datetime('now'))
            ON CONFLICT(thread_id) DO UPDATE SET
                message_count = email_threads.message_count + 1,
                last_message_at = datetime('now');
        """, (thread_id, professor_id, subject))
        return thread_id

    def save_email_message(self, msg: EmailMessage) -> None:
        self.conn.execute("""
            INSERT INTO email_messages (
                message_id, thread_id, professor_id, direction, sender, recipient,
                subject, body, in_reply_to, references_header, sent_received_at, raw_headers
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(message_id) DO UPDATE SET
                body = excluded.body,
                raw_headers = excluded.raw_headers;
        """, (
            msg.message_id, msg.thread_id, msg.professor_id, msg.direction,
            msg.sender, msg.recipient, msg.subject, msg.body, msg.in_reply_to,
            msg.references_header, msg.sent_received_at, msg.raw_headers
        ))

    def get_email_message(self, message_id: str) -> Optional[Dict[str, Any]]:
        cursor = self.conn.execute("SELECT * FROM email_messages WHERE message_id = ?;", (message_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def list_messages_for_thread(self, thread_id: str) -> List[Dict[str, Any]]:
        cursor = self.conn.execute(
            "SELECT * FROM email_messages WHERE thread_id = ? ORDER BY sent_received_at ASC;",
            (thread_id,)
        )
        return [dict(row) for row in cursor.fetchall()]

    # --------------------------------------------------------------------------
    # Reply Classifications & Contextual Drafts
    # --------------------------------------------------------------------------
    def save_reply_classification(self, rec: ReplyClassificationRecord) -> None:
        self.conn.execute("""
            INSERT INTO reply_classifications (
                classification_id, message_id, professor_id, category, confidence,
                reasoning, action_required, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(classification_id) DO UPDATE SET
                category = excluded.category,
                confidence = excluded.confidence,
                reasoning = excluded.reasoning,
                action_required = excluded.action_required;
        """, (
            rec.classification_id, rec.message_id, rec.professor_id, rec.category,
            rec.confidence, rec.reasoning, rec.action_required, rec.created_at
        ))

    def save_reply_draft(self, draft: ReplyDraftRecord) -> str:
        self.conn.execute("""
            INSERT INTO reply_drafts (
                reply_draft_id, thread_id, reply_to_message_id, professor_id,
                draft_body, word_count, status, created_at, human_approved_at,
                human_reviewer, human_sent_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(reply_draft_id) DO UPDATE SET
                draft_body = excluded.draft_body,
                word_count = excluded.word_count,
                status = excluded.status,
                human_approved_at = excluded.human_approved_at,
                human_reviewer = excluded.human_reviewer,
                human_sent_at = excluded.human_sent_at;
        """, (
            draft.reply_draft_id, draft.thread_id, draft.reply_to_message_id,
            draft.professor_id, draft.draft_body, draft.word_count, draft.status,
            draft.created_at, draft.human_approved_at, draft.human_reviewer, draft.human_sent_at
        ))
        return draft.reply_draft_id

    def list_reply_drafts(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        query = """
            SELECT rd.*, p.full_name as professor_name, p.university_name, p.email as professor_email,
                   rc.category as reply_category, rc.reasoning as reply_reasoning
            FROM reply_drafts rd
            JOIN professors p ON rd.professor_id = p.professor_id
            LEFT JOIN reply_classifications rc ON rd.reply_to_message_id = rc.message_id
        """
        params: List[Any] = []
        if status:
            query += " WHERE rd.status = ?"
            params.append(status)
        query += " ORDER BY rd.created_at DESC;"

        cursor = self.conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    # --------------------------------------------------------------------------
    # Follow-Up Drafts
    # --------------------------------------------------------------------------
    def save_followup_draft(self, draft: FollowUpDraftRecord) -> str:
        self.conn.execute("""
            INSERT INTO followup_drafts (
                followup_draft_id, professor_id, original_message_id, draft_body,
                word_count, scheduled_after_days, status, created_at,
                human_approved_at, human_reviewer, human_sent_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(followup_draft_id) DO UPDATE SET
                draft_body = excluded.draft_body,
                word_count = excluded.word_count,
                status = excluded.status,
                human_approved_at = excluded.human_approved_at,
                human_reviewer = excluded.human_reviewer,
                human_sent_at = excluded.human_sent_at;
        """, (
            draft.followup_draft_id, draft.professor_id, draft.original_message_id,
            draft.draft_body, draft.word_count, draft.scheduled_after_days, draft.status,
            draft.created_at, draft.human_approved_at, draft.human_reviewer, draft.human_sent_at
        ))
        return draft.followup_draft_id

    def list_followup_drafts(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        query = """
            SELECT fd.*, p.full_name as professor_name, p.university_name, p.email as professor_email
            FROM followup_drafts fd
            JOIN professors p ON fd.professor_id = p.professor_id
        """
        params: List[Any] = []
        if status:
            query += " WHERE fd.status = ?"
            params.append(status)
        query += " ORDER BY fd.created_at DESC;"

        cursor = self.conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    # --------------------------------------------------------------------------
    # System Aggregates & Dashboard Metrics
    # --------------------------------------------------------------------------
    def get_dashboard_metrics(self) -> Dict[str, Any]:
        """Aggregate high-level metrics for the operator dashboard."""
        total_prospects = self.conn.execute("SELECT COUNT(*) FROM professors;").fetchone()[0]
        verified_emails = self.conn.execute(
            "SELECT COUNT(*) FROM professors WHERE email_verification_status = 'VERIFIED' AND email IS NOT NULL AND email != '';"
        ).fetchone()[0]
        total_universities = self.conn.execute("SELECT COUNT(*) FROM universities;").fetchone()[0]
        
        status_counts = {}
        for row in self.conn.execute("SELECT outreach_status, COUNT(*) as cnt FROM professors GROUP BY outreach_status;").fetchall():
            status_counts[row["outreach_status"]] = row["cnt"]

        drafts_awaiting_review = self.conn.execute(
            "SELECT COUNT(*) FROM outreach_drafts WHERE status = 'DRAFT';"
        ).fetchone()[0]
        drafts_approved = self.conn.execute(
            "SELECT COUNT(*) FROM outreach_drafts WHERE status = 'APPROVED';"
        ).fetchone()[0]
        drafts_dispatched = self.conn.execute(
            "SELECT COUNT(*) FROM outreach_drafts WHERE status = 'SENT';"
        ).fetchone()[0]

        reply_drafts_pending = self.conn.execute(
            "SELECT COUNT(*) FROM reply_drafts WHERE status = 'AWAITING_HUMAN_REVIEW';"
        ).fetchone()[0]
        followup_drafts_pending = self.conn.execute(
            "SELECT COUNT(*) FROM followup_drafts WHERE status = 'AWAITING_HUMAN_REVIEW';"
        ).fetchone()[0]
        suppressed_count = self.conn.execute("SELECT COUNT(*) FROM suppression_list;").fetchone()[0]

        replies_breakdown = {}
        for row in self.conn.execute("SELECT category, COUNT(*) as cnt FROM reply_classifications GROUP BY category;").fetchall():
            replies_breakdown[row["category"]] = row["cnt"]

        return {
            "total_prospects": total_prospects,
            "verified_emails": verified_emails,
            "total_universities": total_universities,
            "status_counts": status_counts,
            "drafts_awaiting_review": drafts_awaiting_review,
            "drafts_approved": drafts_approved,
            "drafts_dispatched": drafts_dispatched,
            "reply_drafts_pending": reply_drafts_pending,
            "followup_drafts_pending": followup_drafts_pending,
            "suppressed_count": suppressed_count,
            "replies_breakdown": replies_breakdown,
        }
