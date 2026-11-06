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
import logging
from dataclasses import dataclass, field
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
