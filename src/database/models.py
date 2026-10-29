"""
Data Models and Entity Schemas for Outreach Intelligence Engine.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Professor:
    """Represents a professor candidate in the authoritative database."""
    professor_id: str
    university_name: str
    country: str
    full_name: str
    email: str
    title: str = ""
    department: str = ""
    qs_rank: Optional[int] = None
    processing_sequence: Optional[int] = None
    email_verification_status: str = "UNVERIFIED"
    verification_source_url: str = ""
    faculty_profile_url: str = ""
    research_areas: str = ""
    research_overlap: str = ""
    cv_skill_project: str = ""
    potential_contribution: str = ""
    evidence_url: str = ""
    date_verified: str = ""
    notes: str = ""
    outreach_status: str = "NEW"  # NEW, DRAFT_READY, APPROVED, QUEUED, SENT, REPLIED, DECLINED, BOUNCED, OPTED_OUT, SUPPRESSED
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)


@dataclass
class University:
    """Represents university-level screening and directory status."""
    university_name: str
    country: str
    qs_rank: Optional[int] = None
    processing_sequence: Optional[int] = None
    city: str = ""
    official_url: str = ""
    review_status: str = "UNREVIEWED"
    departments_reviewed: str = ""
    directories_reviewed: str = ""
    profiles_screened: int = 0
    professors_shortlisted: int = 0
    verified_emails: int = 0
    unverified_emails: int = 0
    timezone_iana: str = "UTC"
    last_updated: str = field(default_factory=utc_now_iso)


@dataclass
class OutreachDraft:
    """First-contact outreach draft prepared for a professor."""
    draft_id: str
    professor_id: str
    recipient_email: str
    subject: str
    body_text: str
    word_count: int
    status: str = "DRAFT"  # DRAFT, APPROVED, REJECTED, QUEUED, SENT
    created_at: str = field(default_factory=utc_now_iso)
    approved_at: Optional[str] = None
    approved_by: Optional[str] = None
    dispatched_at: Optional[str] = None


@dataclass
class EmailThread:
    """Tracks message threads corresponding to an outreach exchange."""
    thread_id: str
    professor_id: str
    subject: str
    status: str = "ACTIVE"  # ACTIVE, REPLIED, RESOLVED, CLOSED, SUPPRESSED
    message_count: int = 1
    last_message_at: str = field(default_factory=utc_now_iso)
    created_at: str = field(default_factory=utc_now_iso)


@dataclass
class EmailMessage:
    """Individual email message sent or received."""
    message_id: str  # RFC 2822 Message-ID
    thread_id: str
    professor_id: Optional[str]
    direction: str  # OUTBOUND, INBOUND
    sender: str
    recipient: str
    subject: str
    body: str
    in_reply_to: Optional[str] = None
    references_header: Optional[str] = None
    sent_received_at: str = field(default_factory=utc_now_iso)
    raw_headers: Optional[str] = None


@dataclass
class ReplyClassificationRecord:
    """Classification details for an incoming message."""
    classification_id: str
    message_id: str
    professor_id: str
    category: str  # INTEREST, REJECTION, REFERRAL, INFO_REQUEST, MEETING_INVITE, FUNDING_QUESTION, TECHNICAL_QUESTION, AUTO_REPLY, BOUNCE, OPT_OUT
    confidence: float
    reasoning: str
    action_required: str
    created_at: str = field(default_factory=utc_now_iso)


@dataclass
class ReplyDraftRecord:
    """Human-review reply draft prepared by AI in response to professor's email."""
    reply_draft_id: str
    thread_id: str
    reply_to_message_id: str
    professor_id: str
    draft_body: str
    word_count: int
    status: str = "AWAITING_HUMAN_REVIEW"  # AWAITING_HUMAN_REVIEW, HUMAN_APPROVED, HUMAN_SENT, REJECTED
    created_at: str = field(default_factory=utc_now_iso)
    human_approved_at: Optional[str] = None
    human_reviewer: Optional[str] = None
    human_sent_at: Optional[str] = None


@dataclass
class FollowUpDraftRecord:
    """User-controlled follow-up draft for unanswered first contacts."""
    followup_draft_id: str
    professor_id: str
    original_message_id: str
    draft_body: str
    word_count: int
    scheduled_after_days: int = 7
    status: str = "AWAITING_HUMAN_REVIEW"  # AWAITING_HUMAN_REVIEW, HUMAN_APPROVED, HUMAN_SENT, DISMISSED
    created_at: str = field(default_factory=utc_now_iso)
    human_approved_at: Optional[str] = None
    human_reviewer: Optional[str] = None
    human_sent_at: Optional[str] = None


@dataclass
class SuppressionRecord:
    """Identifies addresses or domains barred from fresh outreach."""
    suppression_id: str
    email: Optional[str] = None
    domain: Optional[str] = None
    reason: str = "MANUAL"  # PREVIOUSLY_CONTACTED, INSTITUTIONAL_SELF, REPLY_RECEIVED, DECLINED, BOUNCED, OPTED_OUT, MANUAL
    notes: str = ""
    created_at: str = field(default_factory=utc_now_iso)
