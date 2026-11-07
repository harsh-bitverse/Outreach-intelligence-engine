"""
Opportunity Intelligence Engine: Central Inbound Response Intelligence & Outcome Classifier.

Implements:
1. 14-Intent Classification of inbound professor and company email replies:
   - POSITIVE_INTEREST
   - INTERVIEW_INVITATION
   - FUNDING_DISCUSSION
   - CONDITIONAL_INTEREST
   - REQUEST_FOR_CV_OR_DOCUMENTS
   - REQUEST_FOR_TECHNICAL_TASK
   - REFERRAL
   - APPLICATION_INSTRUCTIONS
   - CAPACITY_FULL
   - FUNDING_UNAVAILABLE
   - REJECTION
   - OUT_OF_OFFICE
   - ACKNOWLEDGEMENT_ONLY
   - OPT_OUT
   - AMBIGUOUS_NEEDS_REVIEW

2. High-Priority Response Queue:
   - Automatically surfaces positive signals, interview requests, funding discussions,
     and action-required deadlines into dedicated queues and workbook tracking.

3. Application Acknowledgement & Confirmation Correlator:
   - Correlates ATS automated receipt emails (Greenhouse, Ashby, Lever, University portals)
     with tracked application records in the database.
"""

import re
import json
import logging
import sqlite3
from pathlib import Path
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ReplyIntent(str, Enum):
    POSITIVE_INTEREST = "POSITIVE_INTEREST"
    INTERVIEW_INVITATION = "INTERVIEW_INVITATION"
    FUNDING_DISCUSSION = "FUNDING_DISCUSSION"
    CONDITIONAL_INTEREST = "CONDITIONAL_INTEREST"
    REQUEST_FOR_CV_OR_DOCUMENTS = "REQUEST_FOR_CV_OR_DOCUMENTS"
    REQUEST_FOR_TECHNICAL_TASK = "REQUEST_FOR_TECHNICAL_TASK"
    REFERRAL = "REFERRAL"
    APPLICATION_INSTRUCTIONS = "APPLICATION_INSTRUCTIONS"
    CAPACITY_FULL = "CAPACITY_FULL"
    FUNDING_UNAVAILABLE = "FUNDING_UNAVAILABLE"
    REJECTION = "REJECTION"
    OUT_OF_OFFICE = "OUT_OF_OFFICE"
    ACKNOWLEDGEMENT_ONLY = "ACKNOWLEDGEMENT_ONLY"
    OPT_OUT = "OPT_OUT"
    AMBIGUOUS_NEEDS_REVIEW = "AMBIGUOUS_NEEDS_REVIEW"


@dataclass
class ReplyClassificationResult:
    classification: ReplyIntent
    confidence: float
    is_high_priority: bool
    summary: str
    action_required: str
    extracted_deadline: Optional[str] = None
    thread_id: Optional[str] = None
    sender_email: Optional[str] = None


@dataclass
class HighPriorityQueueItem:
    item_id: str
    opportunity_id: str
    sender_email: str
    organization_name: str
    intent: ReplyIntent
    summary: str
    snippet: str
    received_at: str
    deadline: Optional[str] = None
    status: str = "PENDING_ACTION"


class ResponseIntelligenceEngine:
    """
    Classifies incoming email replies, tracks ATS confirmations, and prioritizes actionable outcomes.
    """

    # Intent keyword heuristics
    OUT_OF_OFFICE_PATTERNS = [
        r"out of (?:the )?office",
        r"automatic reply",
        r"auto(?:mated)? response",
        r"away from (?:my )?email",
        r"on (?:annual|paternity|maternity|medical) leave",
        r"back on \d{1,2}",
        r"will respond when I return"
    ]

    OPT_OUT_PATTERNS = [
        r"unsubscribe",
        r"remove me from (?:your|this) list",
        r"stop emailing me",
        r"do not contact me (?:again)?",
        r"please take me off"
    ]

    INTERVIEW_PATTERNS = [
        r"(?:set up|schedule|arrange) a (?:call|zoom|interview|chat|meeting)",
        r"time to (?:chat|talk|meet|connect)",
        r"are you available (?:for a|to) (?:call|zoom|interview|chat)",
        r"calendly\.com",
        r"send your availability",
        r"interview invitation",
        r"invitation to interview"
    ]

    FUNDING_PATTERNS = [
        r"(?:stipend|funding|financial support|scholarship|grant|remuneration|salary)",
        r"(?:cover|assist with) your (?:travel|housing|living expenses|accommodation)",
        r"discuss (?:funding|financial) arrangements",
        r"fellowship allowance"
    ]

    POSITIVE_INTEREST_PATTERNS = [
        r"happy to host you",
        r"would be glad to (?:host|have) you",
        r"interested in your (?:profile|background|work|proposal)",
        r"impressed by your (?:work|projects|cv|experience)",
        r"welcome you to (?:our|the) lab",
        r"would love to work with you",
        r"we have an opening for summer 2027",
        r"fits well with our current project"
    ]

    CONDITIONAL_INTEREST_PATTERNS = [
        r"if you can secure (?:external|your own) funding",
        r"subject to (?:departmental|faculty|dean|visa) approval",
        r"pending available (?:space|desk|bench)",
        r"contingent on (?:funding|grant|visa)"
    ]

    REQUEST_DOCS_PATTERNS = [
        r"please send (?:your|a copy of) (?:official )?(?:transcript|cv|resume|recommendation|references)",
        r"can you share your (?:transcript|portfolio|codebase|preprints)",
        r"send through your academic records"
    ]

    REQUEST_TASK_PATTERNS = [
        r"take-home (?:assignment|task|challenge|test)",
        r"complete this (?:assessment|technical test|coding exercise|hackerrank|codesignal)",
        r"technical challenge"
    ]

    REFERRAL_PATTERNS = [
        r"suggest you (?:contact|email|reach out to) (?:prof|dr|colleague)",
        r"cc'?ing (?:prof|dr|my colleague)",
        r"passing this to (?:prof|dr|our team)",
        r"you should reach out to"
    ]

    APPLICATION_INSTRUCTIONS_PATTERNS = [
        r"please apply (?:formally|officially|through|via) (?:our|the) portal",
        r"official application (?:link|system|form)",
        r"submit an application at",
        r"summer research program portal"
    ]

    CAPACITY_FULL_PATTERNS = [
        r"(?:lab|group|team) is (?:currently )?(?:full|at capacity)",
        r"not taking (?:any )?(?:new )?(?:interns|students|scholars)",
        r"no (?:capacity|openings|vacancies) for summer 2027",
        r"already selected our interns"
    ]

    FUNDING_UNAVAILABLE_PATTERNS = [
        r"no (?:available )?(?:funding|budget|financial support|stipend)",
        r"unable to offer (?:a )?paid",
        r"cannot offer (?:financial|stipend) support",
        r"unfunded"
    ]

    REJECTION_PATTERNS = [
        r"unable to offer you",
        r"cannot accommodate your request",
        r"decided to pursue other candidates",
        r"not able to offer",
        r"not a good fit at this time",
        r"regret to inform you"
    ]

    ACKNOWLEDGEMENT_PATTERNS = [
        r"thank you for (?:your )?(?:email|reaching out|interest)",
        r"we have received your (?:inquiry|email|application)",
        r"will keep (?:your resume|your details|this) on file",
        r"duly noted"
    ]

    def classify_reply(
        self,
        subject: str,
        body: str,
        sender_email: Optional[str] = None
    ) -> ReplyClassificationResult:
        """
        Classifies an inbound reply message into one of the 14 discrete intents.
        Applies rigorous distinguishing logic: ordinary politeness is NEVER marked as positive interest.
        """
        text = f"{subject} \n {body}".lower()

        # 1. Check Out of Office
        if any(re.search(pat, text) for pat in self.OUT_OF_OFFICE_PATTERNS):
            return ReplyClassificationResult(
                classification=ReplyIntent.OUT_OF_OFFICE,
                confidence=0.98,
                is_high_priority=False,
                summary="Out of office or automated response received.",
                action_required="WAIT_AND_FOLLOW_UP_AFTER_RETURN",
                sender_email=sender_email
            )

        # 2. Check Opt-Out
        if any(re.search(pat, text) for pat in self.OPT_OUT_PATTERNS):
            return ReplyClassificationResult(
                classification=ReplyIntent.OPT_OUT,
                confidence=0.99,
                is_high_priority=False,
                summary="Recipient explicitly requested opt-out or removal from communication.",
                action_required="SUPPRESS_CONTACT_IMMEDIATELY",
                sender_email=sender_email
            )

        # 3. Check Interview Invitation (High Priority)
        if any(re.search(pat, text) for pat in self.INTERVIEW_PATTERNS):
            return ReplyClassificationResult(
                classification=ReplyIntent.INTERVIEW_INVITATION,
                confidence=0.95,
                is_high_priority=True,
                summary="Recipient invited applicant to an interview, introductory call, or meeting.",
                action_required="SCHEDULE_INTERVIEW_CALL",
                sender_email=sender_email
            )

        # 4. Check Funding Discussion (High Priority)
        if any(re.search(pat, text) for pat in self.FUNDING_PATTERNS) and any(
            re.search(p, text) for p in [r"discuss", r"offer", r"provide", r"assist", r"stipend of"]
        ):
            return ReplyClassificationResult(
                classification=ReplyIntent.FUNDING_DISCUSSION,
                confidence=0.92,
                is_high_priority=True,
                summary="Recipient explicitly opened discussion regarding funding, stipend, or travel support.",
                action_required="REVIEW_FUNDING_TERMS_AND_REPLY",
                sender_email=sender_email
            )

        # 5. Check Technical Task / Assessment (High Priority)
        if any(re.search(pat, text) for pat in self.REQUEST_TASK_PATTERNS):
            return ReplyClassificationResult(
                classification=ReplyIntent.REQUEST_FOR_TECHNICAL_TASK,
                confidence=0.94,
                is_high_priority=True,
                summary="Recipient requested technical assessment, coding challenge, or take-home task.",
                action_required="COMPLETE_TECHNICAL_TASK",
                sender_email=sender_email
            )

        # 6. Check Request for CV / Documents (High Priority)
        if any(re.search(pat, text) for pat in self.REQUEST_DOCS_PATTERNS):
            return ReplyClassificationResult(
                classification=ReplyIntent.REQUEST_FOR_CV_OR_DOCUMENTS,
                confidence=0.93,
                is_high_priority=True,
                summary="Recipient requested official transcripts, references, or additional research materials.",
                action_required="PREPARE_AND_SEND_REQUESTED_DOCUMENTS",
                sender_email=sender_email
            )

        # 7. Check Positive Interest (High Priority)
        if any(re.search(pat, text) for pat in self.POSITIVE_INTEREST_PATTERNS):
            return ReplyClassificationResult(
                classification=ReplyIntent.POSITIVE_INTEREST,
                confidence=0.91,
                is_high_priority=True,
                summary="Recipient expressed direct positive interest in research collaboration or hosting.",
                action_required="RESPOND_PROMPTLY_WITH_PROJECT_ALIGNMENT",
                sender_email=sender_email
            )

        # 8. Check Conditional Interest
        if any(re.search(pat, text) for pat in self.CONDITIONAL_INTEREST_PATTERNS):
            return ReplyClassificationResult(
                classification=ReplyIntent.CONDITIONAL_INTEREST,
                confidence=0.88,
                is_high_priority=True,
                summary="Conditional interest expressed contingent on external funding, scholarships, or visa rules.",
                action_required="EVALUATE_EXTERNAL_FUNDING_OPTIONS",
                sender_email=sender_email
            )

        # 9. Check Referral
        if any(re.search(pat, text) for pat in self.REFERRAL_PATTERNS):
            return ReplyClassificationResult(
                classification=ReplyIntent.REFERRAL,
                confidence=0.89,
                is_high_priority=True,
                summary="Recipient referred applicant to a colleague, co-PI, or related laboratory.",
                action_required="REACH_OUT_TO_REFERRED_FACULTY",
                sender_email=sender_email
            )

        # 10. Check Application Instructions
        if any(re.search(pat, text) for pat in self.APPLICATION_INSTRUCTIONS_PATTERNS):
            return ReplyClassificationResult(
                classification=ReplyIntent.APPLICATION_INSTRUCTIONS,
                confidence=0.90,
                is_high_priority=False,
                summary="Recipient directed applicant to submit through formal portal or summer fellowship system.",
                action_required="SUBMIT_THROUGH_FORMAL_PORTAL",
                sender_email=sender_email
            )

        # 11. Check Capacity Full
        if any(re.search(pat, text) for pat in self.CAPACITY_FULL_PATTERNS):
            return ReplyClassificationResult(
                classification=ReplyIntent.CAPACITY_FULL,
                confidence=0.92,
                is_high_priority=False,
                summary="Laboratory or group is at maximum capacity with no openings for Summer 2027.",
                action_required="CLOSE_RECORD_NO_OPENINGS",
                sender_email=sender_email
            )

        # 12. Check Funding Unavailable
        if any(re.search(pat, text) for pat in self.FUNDING_UNAVAILABLE_PATTERNS):
            return ReplyClassificationResult(
                classification=ReplyIntent.FUNDING_UNAVAILABLE,
                confidence=0.90,
                is_high_priority=False,
                summary="Recipient indicated no funding or stipend is available for visiting scholars.",
                action_required="NOTE_UNFUNDED_STATUS",
                sender_email=sender_email
            )

        # 13. Check Rejection
        if any(re.search(pat, text) for pat in self.REJECTION_PATTERNS):
            return ReplyClassificationResult(
                classification=ReplyIntent.REJECTION,
                confidence=0.92,
                is_high_priority=False,
                summary="Recipient declined inquiry for Summer 2027.",
                action_required="UPDATE_STATUS_REJECTED",
                sender_email=sender_email
            )

        # 14. Check Acknowledgement Only (Polite non-substantive response)
        if any(re.search(pat, text) for pat in self.ACKNOWLEDGEMENT_PATTERNS):
            return ReplyClassificationResult(
                classification=ReplyIntent.ACKNOWLEDGEMENT_ONLY,
                confidence=0.85,
                is_high_priority=False,
                summary="Polite receipt acknowledgement without substantive commitment or interest.",
                action_required="LOG_ACKNOWLEDGEMENT_MONITOR_FOR_FOLLOWUP",
                sender_email=sender_email
            )

        # Fallback: Ambiguous / Needs Review
        return ReplyClassificationResult(
            classification=ReplyIntent.AMBIGUOUS_NEEDS_REVIEW,
            confidence=0.50,
            is_high_priority=False,
            summary="Uncertain reply content requiring human operator inspection.",
            action_required="MANUAL_REVIEW_REQUIRED",
            sender_email=sender_email
        )

    def correlate_ats_confirmation(
        self,
        email_subject: str,
        email_body: str,
        sender_address: str
    ) -> Optional[Dict[str, Any]]:
        """
        Detects ATS confirmation receipts and matches them to application tracker records.
        """
        combined = f"{email_subject} \n {email_body}"
        sender_lower = sender_address.lower()

        ats_system = None
        if "greenhouse.io" in sender_lower or "greenhouse" in combined.lower():
            ats_system = "GREENHOUSE"
        elif "ashbyhq.com" in sender_lower or "ashby" in combined.lower():
            ats_system = "ASHBY"
        elif "lever.co" in sender_lower or "lever" in combined.lower():
            ats_system = "LEVER"
        elif "myworkday" in sender_lower or "workday" in combined.lower():
            ats_system = "WORKDAY"
        elif any(term in combined.lower() for term in ["application received", "thank you for applying", "submission confirmation"]):
            ats_system = "INSTITUTIONAL_PORTAL"

        if not ats_system:
            return None

        # Extract confirmation / requisition ID where present
        conf_match = re.search(r"(?:confirmation|application|reference|req|id)[\s#:]+([A-Z0-9\-_]{4,20})", combined, re.IGNORECASE)
        conf_id = conf_match.group(1) if conf_match else "CONF-CONFIRMED-RECEIPT"

        return {
            "is_ats_confirmation": True,
            "ats_system": ats_system,
            "confirmation_id": conf_id,
            "subject": email_subject,
            "sender": sender_address,
            "derived_status": "APPLICATION_ACKNOWLEDGED",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }


class ProductionOutcomeReconciler:
    """
    Connects to the production database in READ-ONLY mode and reconciles real recorded
    IMAP outreach events, replies, and bounces against the Response Intelligence Engine.
    Guarantees zero mutation of production reply states.
    """

    def __init__(self, prod_db_path: Optional[Path] = None):
        self.prod_db_path = prod_db_path or (
            Path(__file__).parent.parent.parent / "data" / "outreach_engine.db"
        )
        self.classifier = ResponseIntelligenceEngine()
        self.checkpoint_timestamp: Optional[str] = None

    def reconcile_production_outcomes(self) -> Dict[str, Any]:
        """
        Executes read-only reconciliation of production outbound delivery tracker and inbound messages.
        Returns classified summary of positive replies, interviews, bounces, and exceptions.
        """
        if not self.prod_db_path.exists():
            raise FileNotFoundError(f"Production database not found at {self.prod_db_path}")

        conn = sqlite3.connect(f"file:{self.prod_db_path.resolve()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # 1. Inspect outbound delivery tracker replies
        cur.execute("""
            SELECT id, professor_id, university_name, recipient_email, delivery_status,
                   tracking_status, bounce_diagnostic, updated_at
            FROM outbound_delivery_tracker
            WHERE delivery_status = 'REPLIED';
        """)
        replied_tracker_rows = [dict(r) for r in cur.fetchall()]

        # 2. Inspect recorded inbound messages
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='email_messages';")
        has_msg_table = bool(cur.fetchone())

        inbound_messages = []
        if has_msg_table:
            cur.execute("""
                SELECT message_id, thread_id, professor_id, direction, sender,
                       recipient, subject, body, sent_received_at
                FROM email_messages
                WHERE direction = 'INBOUND';
            """)
            inbound_messages = [dict(r) for r in cur.fetchall()]

        # 3. Inspect bounces
        cur.execute("""
            SELECT id, professor_id, university_name, recipient_email, delivery_status,
                   bounce_category, bounce_diagnostic
            FROM outbound_delivery_tracker
            WHERE delivery_status LIKE '%BOUNCE%';
        """)
        bounce_rows = [dict(r) for r in cur.fetchall()]

        conn.close()

        # Classify all inbound messages
        classified_replies = []
        high_priority_items = []
        exceptions_for_review = []

        for msg in inbound_messages:
            res = self.classifier.classify_reply(
                subject=msg.get("subject", ""),
                body=msg.get("body", ""),
                sender_email=msg.get("sender")
            )
            item = {
                "message_id": msg.get("message_id"),
                "sender": msg.get("sender"),
                "professor_id": msg.get("professor_id"),
                "subject": msg.get("subject"),
                "classification": res.classification.value,
                "confidence": res.confidence,
                "is_high_priority": res.is_high_priority,
                "action_required": res.action_required,
                "snippet": (msg.get("body") or "")[:200]
            }
            classified_replies.append(item)
            if res.is_high_priority:
                high_priority_items.append(item)
            if res.confidence < 0.70 or res.classification == ReplyIntent.AMBIGUOUS_NEEDS_REVIEW:
                exceptions_for_review.append(item)

        # Map tracker replied rows
        tracker_classified = []
        for tr in replied_tracker_rows:
            # Match with inbound message if available
            matching_inbound = next(
                (m for m in inbound_messages if m.get("sender", "").strip().lower() == tr["recipient_email"].strip().lower()),
                None
            )
            if matching_inbound:
                tr_intent = next(
                    (c["classification"] for c in classified_replies if c["message_id"] == matching_inbound["message_id"]),
                    "POSITIVE_INTEREST"
                )
            else:
                # Default confirmed reply from tracker telemetry
                tr_intent = "CONFIRMED_REPLY_RECEIVED"

            tracker_classified.append({
                "tracker_id": tr["id"],
                "professor_id": tr["professor_id"],
                "university": tr["university_name"],
                "recipient_email": tr["recipient_email"],
                "intent": tr_intent,
                "has_inbound_body": bool(matching_inbound),
                "resolved_at": tr["updated_at"]
            })

        self.checkpoint_timestamp = datetime.now(timezone.utc).isoformat()

        return {
            "status": "RECONCILIATION_SUCCESS",
            "production_read_only_verified": True,
            "total_tracker_replies": len(replied_tracker_rows),
            "total_inbound_messages_classified": len(inbound_messages),
            "total_bounces_monitored": len(bounce_rows),
            "high_priority_positive_signals_count": len(high_priority_items),
            "high_priority_items": high_priority_items,
            "exceptions_requiring_review": exceptions_for_review,
            "tracker_classified_replies": tracker_classified,
            "checkpoint_timestamp": self.checkpoint_timestamp
        }


class InboundRecordClassification(str, Enum):
    CONFIRMED_RETRIEVED_AND_CLASSIFIED = "CONFIRMED_RETRIEVED_AND_CLASSIFIED"
    STATUS_MARKED_AWAITING_BODY_RETRIEVAL = "STATUS_MARKED_AWAITING_BODY_RETRIEVAL"
    UNMATCHED_INBOUND = "UNMATCHED_INBOUND"
    ATS_CONFIRMATION_RECEIPT = "ATS_CONFIRMATION_RECEIPT"


@dataclass
class UIDCheckpoint:
    mailbox: str
    uid_validity: int
    last_seen_uid: int
    checkpoint_timestamp: str
    processed_count: int
    is_valid: bool = True
    uidvalidity_changed: bool = False


class InboundMessageIndexer:
    """
    Category-independent inbound message indexer supporting:
    1. Parsing RFC822 headers: In-Reply-To, References, Message-ID, Subject, From, To
    2. Correlating inbound threads against:
       - Outbound sent emails (via In-Reply-To, References, sender email)
       - Portal / ATS application confirmation receipts (via CONF-*, REQ-* or sender domain)
    3. Maintaining durable IMAP UID checkpoints with UIDVALIDITY cache & duplicate suppression.
    4. Categorizing records strictly into:
       - CONFIRMED_RETRIEVED_AND_CLASSIFIED (payload downloaded and parsed)
       - STATUS_MARKED_AWAITING_BODY_RETRIEVAL (status logged in outbound tracker, but raw RFC822 body not yet fetched)
    5. Surfacing high-priority interview queue (with automated_reply_permitted=False).
    """

    def __init__(self, checkpoint_file: Optional[Path] = None):
        self.classifier = ResponseIntelligenceEngine()
        self.checkpoint_file = checkpoint_file or (
            Path(__file__).parent.parent.parent / "data" / "imap_uid_checkpoint.json"
        )
        self.checkpoints: Dict[str, UIDCheckpoint] = self._load_checkpoints()

    def _load_checkpoints(self) -> Dict[str, UIDCheckpoint]:
        if self.checkpoint_file.exists():
            try:
                with open(self.checkpoint_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return {
                        mb: UIDCheckpoint(**cp) for mb, cp in data.items()
                    }
            except Exception as e:
                logger.warning(f"Could not load IMAP UID checkpoint file: {e}")
        return {}

    def save_checkpoints(self) -> None:
        self.checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
        data = {mb: asdict(cp) for mb, cp in self.checkpoints.items()}
        with open(self.checkpoint_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def parse_rfc822_headers(self, headers: Dict[str, str]) -> Dict[str, Any]:
        """Parses RFC822 message headers for thread matching."""
        norm = {k.lower(): v for k, v in headers.items()}

        msg_id = norm.get("message-id", "").strip("<> ").strip()
        in_reply_to = norm.get("in-reply-to", "").strip("<> ").strip()
        raw_refs = norm.get("references", "")
        refs = [r.strip("<> ").strip() for r in re.findall(r"<([^>]+)>", raw_refs)]
        if not refs and raw_refs:
            refs = [r.strip() for r in raw_refs.split() if r.strip()]

        from_hdr = norm.get("from", "")
        from_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", from_hdr)
        from_email = from_match.group(0).lower() if from_match else from_hdr.lower().strip()

        to_hdr = norm.get("to", "")
        to_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", to_hdr)
        to_email = to_match.group(0).lower() if to_match else to_hdr.lower().strip()

        subject = norm.get("subject", "").strip()

        return {
            "message_id": msg_id,
            "in_reply_to": in_reply_to,
            "references": refs,
            "sender_email": from_email,
            "recipient_email": to_email,
            "subject": subject
        }

    def extract_application_id(self, text: str) -> Optional[str]:
        """Extracts application or confirmation reference IDs from email body or subject."""
        patterns = [
            r"CONF-[A-Z0-9\-_]{4,24}",
            r"REQ-[A-Z0-9\-_]{4,24}",
            r"APP-[A-Z0-9\-_]{4,24}",
            r"(?:confirmation|application|reference)\s*(?:number|id|#)[:\s]+([A-Z0-9\-_]{4,20})"
        ]
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                res = m.group(0)
                if any(p in res.upper() for p in ["CONF-", "REQ-", "APP-"]):
                    return res
                return m.group(1)
        return None

    def update_uid_checkpoint(
        self,
        mailbox: str,
        uid_validity: int,
        highest_uid: int,
        messages_processed: int = 0
    ) -> UIDCheckpoint:
        """
        Updates IMAP UID checkpoint. Handles UIDVALIDITY resets safely:
        If UIDVALIDITY differs from stored value, flags uidvalidity_changed=True.
        """
        existing = self.checkpoints.get(mailbox)
        uidvalidity_changed = False
        if existing and existing.uid_validity != uid_validity:
            uidvalidity_changed = True
            logger.warning(
                f"IMAP UIDVALIDITY changed for {mailbox} from {existing.uid_validity} to {uid_validity}. "
                "Local UIDs invalidated; full resync required."
            )

        new_cp = UIDCheckpoint(
            mailbox=mailbox,
            uid_validity=uid_validity,
            last_seen_uid=highest_uid,
            checkpoint_timestamp=datetime.now(timezone.utc).isoformat(),
            processed_count=(existing.processed_count if existing and not uidvalidity_changed else 0) + messages_processed,
            is_valid=not uidvalidity_changed,
            uidvalidity_changed=uidvalidity_changed
        )
        self.checkpoints[mailbox] = new_cp
        self.save_checkpoints()
        return new_cp

    def index_inbound_record(
        self,
        headers: Dict[str, str],
        body: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Indexes an inbound email record and categorizes it:
        - If body is present: CONFIRMED_RETRIEVED_AND_CLASSIFIED
        - If body is absent (tracker status only): STATUS_MARKED_AWAITING_BODY_RETRIEVAL
        - Detects ATS confirmations.
        """
        parsed_h = self.parse_rfc822_headers(headers)
        app_id = self.extract_application_id(f"{parsed_h['subject']} {body or ''}")

        # Check ATS confirmation
        ats_info = self.classifier.correlate_ats_confirmation(
            email_subject=parsed_h["subject"],
            email_body=body or "",
            sender_address=parsed_h["sender_email"]
        )

        if ats_info:
            return {
                "message_id": parsed_h["message_id"],
                "in_reply_to": parsed_h["in_reply_to"],
                "references": parsed_h["references"],
                "sender_email": parsed_h["sender_email"],
                "recipient_email": parsed_h["recipient_email"],
                "subject": parsed_h["subject"],
                "application_id": app_id or ats_info["confirmation_id"],
                "ats_system": ats_info["ats_system"],
                "classification_status": InboundRecordClassification.ATS_CONFIRMATION_RECEIPT,
                "intent": ReplyIntent.ACKNOWLEDGEMENT_ONLY,
                "is_high_priority": False,
                "requires_manual_action": False,
                "automated_reply_permitted": False,
                "raw_body_available": bool(body),
                "summary": f"ATS acknowledgement from {ats_info['ats_system']} (ID: {ats_info['confirmation_id']})",
                "indexed_at": datetime.now(timezone.utc).isoformat()
            }

        if not body:
            return {
                "message_id": parsed_h["message_id"],
                "in_reply_to": parsed_h["in_reply_to"],
                "references": parsed_h["references"],
                "sender_email": parsed_h["sender_email"],
                "recipient_email": parsed_h["recipient_email"],
                "subject": parsed_h["subject"],
                "application_id": app_id,
                "classification_status": InboundRecordClassification.STATUS_MARKED_AWAITING_BODY_RETRIEVAL,
                "intent": None,
                "is_high_priority": False,
                "requires_manual_action": False,
                "automated_reply_permitted": False,
                "raw_body_available": False,
                "summary": "Delivery status marked REPLIED in outbound tracker; raw RFC822 body awaiting IMAP retrieval.",
                "indexed_at": datetime.now(timezone.utc).isoformat()
            }

        # Classify retrieved body
        classification = self.classifier.classify_reply(
            subject=parsed_h["subject"],
            body=body,
            sender_email=parsed_h["sender_email"]
        )

        return {
            "message_id": parsed_h["message_id"],
            "in_reply_to": parsed_h["in_reply_to"],
            "references": parsed_h["references"],
            "sender_email": parsed_h["sender_email"],
            "recipient_email": parsed_h["recipient_email"],
            "subject": parsed_h["subject"],
            "application_id": app_id,
            "classification_status": InboundRecordClassification.CONFIRMED_RETRIEVED_AND_CLASSIFIED,
            "intent": classification.classification,
            "confidence": classification.confidence,
            "is_high_priority": classification.is_high_priority,
            "requires_manual_action": classification.is_high_priority,
            "automated_reply_permitted": False,
            "action_required": classification.action_required,
            "summary": classification.summary,
            "snippet": body[:250],
            "raw_body_available": True,
            "indexed_at": datetime.now(timezone.utc).isoformat()
        }

    def get_high_priority_interview_queue(self, prod_db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
        """
        Extracts high-priority items (such as Prof. Bodner at MIT) requiring manual operator review and reply.
        Strictly enforces automated_reply_permitted = False.
        """
        reconciler = ProductionOutcomeReconciler(prod_db_path=prod_db_path)
        res = reconciler.reconcile_production_outcomes()

        queue = []
        for item in res.get("high_priority_items", []):
            queue.append({
                "message_id": item.get("message_id"),
                "sender": item.get("sender"),
                "professor_id": item.get("professor_id"),
                "subject": item.get("subject"),
                "classification": item.get("classification"),
                "confidence": item.get("confidence"),
                "action_required": item.get("action_required"),
                "automated_reply_permitted": False,
                "manual_action_required": True,
                "recommendation": "OPERATOR_DIRECT_MANUAL_REPLY",
                "snippet": item.get("snippet", "")
            })
        return queue

