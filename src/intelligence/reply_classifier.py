"""
Reply Intelligence and Classification Engine.
Classifies incoming email responses across 10 distinct categories,
determines action requirements, and halts queued outreach.
"""

import re
from typing import Dict, Any, Tuple
from src.database.models import ReplyClassificationRecord


class ReplyCategory:
    INTEREST = "INTEREST"
    REJECTION = "REJECTION"
    REFERRAL = "REFERRAL"
    INFO_REQUEST = "INFO_REQUEST"
    MEETING_INVITE = "MEETING_INVITE"
    FUNDING_QUESTION = "FUNDING_QUESTION"
    TECHNICAL_QUESTION = "TECHNICAL_QUESTION"
    AUTO_REPLY = "AUTO_REPLY"
    BOUNCE = "BOUNCE"
    OPT_OUT = "OPT_OUT"


class ReplyClassifier:
    """Classifies incoming emails using multi-pattern analysis and contextual reasoning."""

    # Categorical Regex Rules
    PATTERNS = {
        ReplyCategory.BOUNCE: [
            r"delivery status notification", r"failure notice", r"undeliverable",
            r"mail delivery failed", r"550 user unknown", r"mailbox not found",
            r"recipient address rejected", r"returned mail", r"status:\s*5\.\d+\.\d+"
        ],
        ReplyCategory.AUTO_REPLY: [
            r"out of office", r"automatic reply", r"auto-reply", r"away from my email",
            r"on annual leave", r"currently on leave", r"sabbatical leave",
            r"will respond when i return", r"automated response"
        ],
        ReplyCategory.OPT_OUT: [
            r"unsubscribe", r"remove me", r"do not email me", r"take me off your list",
            r"stop emailing", r"please do not contact", r"cease contacting"
        ],
        ReplyCategory.MEETING_INVITE: [
            r"zoom link", r"google meet", r"teams meeting", r"schedule a call",
            r"let'?s set up a time", r"available for a chat", r"let'?s talk next week",
            r"calendly\.com", r"brief interview", r"time to speak"
        ],
        ReplyCategory.INFO_REQUEST: [
            r"send (me )?your cv", r"attach your resume", r"academic transcript",
            r"code sample", r"github profile", r"recommendation letter",
            r"share your grades", r"research proposal"
        ],
        ReplyCategory.FUNDING_QUESTION: [
            r"do you have (your own )?funding", r"self-funded", r"external scholarship",
            r"mitacs", r"daad", r"provide a stipend", r"unfunded position",
            r"financial support"
        ],
        ReplyCategory.REFERRAL: [
            r"contact prof", r"forward your email to", r"suggest you write to",
            r"apply through our (portal|department)", r"central admissions",
            r"summer internship program website", r"formal application portal"
        ],
        ReplyCategory.REJECTION: [
            r"no (openings|positions|vacancies)", r"not taking (any )?(interns|students)",
            r"cannot (accept|host|take) you", r"lab is full", r"unable to offer",
            r"too busy to host", r"unfortunately.*cannot", r"regret to inform"
        ],
        ReplyCategory.TECHNICAL_QUESTION: [
            r"what experience do you have with", r"how did you implement",
            r"which framework did you use", r"familiar with", r"benchmark results",
            r"can you explain"
        ],
        ReplyCategory.INTEREST: [
            r"sounds very interesting", r"impressive background", r"would be happy to host",
            r"glad to consider", r"great fit", r"look forward to working",
            r"strong interest", r"very interested in your work"
        ]
    }

    def classify(
        self,
        subject: str,
        body: str,
        headers: Dict[str, str] = None
    ) -> Tuple[str, float, str, str]:
        """
        Classifies an email and returns (category, confidence, reasoning, action_required).
        """
        text = f"{subject}\n{body}".lower()
        headers = headers or {}

        # 1. Header checks for bounce / auto-reply
        if "mailer-daemon" in headers.get("from", "").lower() or "postmaster" in headers.get("from", "").lower():
            return (
                ReplyCategory.BOUNCE, 0.99,
                "Detected Mailer-Daemon bounce envelope.",
                "Mark professor as BOUNCED and suppress recipient permanently."
            )

        if headers.get("auto-submitted") == "auto-replied" or headers.get("x-autoreply") == "yes":
            return (
                ReplyCategory.AUTO_REPLY, 0.95,
                "Auto-submitted auto-reply header present.",
                "Wait for professor's return; no immediate action required."
            )

        # 2. Sequential rule scoring
        scores: Dict[str, float] = {}
        matches: Dict[str, List[str]] = {}

        for category, pattern_list in self.PATTERNS.items():
            matched_terms = []
            for pat in pattern_list:
                if re.search(pat, text, re.IGNORECASE):
                    matched_terms.append(pat)
            if matched_terms:
                scores[category] = len(matched_terms)
                matches[category] = matched_terms

        # Priority resolution
        if ReplyCategory.BOUNCE in scores:
            return (
                ReplyCategory.BOUNCE, 0.98,
                f"Matched delivery bounce markers: {matches[ReplyCategory.BOUNCE]}",
                "Halt queued outreach and suppress recipient."
            )

        if ReplyCategory.OPT_OUT in scores:
            return (
                ReplyCategory.OPT_OUT, 0.98,
                f"Explicit opt-out request detected: {matches[ReplyCategory.OPT_OUT]}",
                "Halt queued outreach and permanently suppress recipient."
            )

        if ReplyCategory.AUTO_REPLY in scores:
            return (
                ReplyCategory.AUTO_REPLY, 0.95,
                f"Out-of-office message detected: {matches[ReplyCategory.AUTO_REPLY]}",
                "Record away status; keep lead open."
            )

        if ReplyCategory.MEETING_INVITE in scores:
            return (
                ReplyCategory.MEETING_INVITE, 0.92,
                f"Meeting or interview invitation detected: {matches[ReplyCategory.MEETING_INVITE]}",
                "High Priority: Prepare meeting acceptance draft for student review."
            )

        if ReplyCategory.REJECTION in scores and ReplyCategory.INTEREST not in scores:
            return (
                ReplyCategory.REJECTION, 0.90,
                f"Explicit rejection/no-openings detected: {matches[ReplyCategory.REJECTION]}",
                "Halt outreach; archive lead as DECLINED."
            )

        if ReplyCategory.INFO_REQUEST in scores:
            return (
                ReplyCategory.INFO_REQUEST, 0.90,
                f"Professor requested documentation: {matches[ReplyCategory.INFO_REQUEST]}",
                "Prepare contextual reply with CV/requested materials for student review."
            )

        if ReplyCategory.REFERRAL in scores:
            return (
                ReplyCategory.REFERRAL, 0.88,
                f"Referral to program or colleague: {matches[ReplyCategory.REFERRAL]}",
                "Prepare acknowledgment draft for student review."
            )

        if ReplyCategory.FUNDING_QUESTION in scores:
            return (
                ReplyCategory.FUNDING_QUESTION, 0.85,
                f"Funding inquiry detected: {matches[ReplyCategory.FUNDING_QUESTION]}",
                "Prepare factual funding clarification draft for student review."
            )

        if ReplyCategory.TECHNICAL_QUESTION in scores:
            return (
                ReplyCategory.TECHNICAL_QUESTION, 0.85,
                f"Technical query detected: {matches[ReplyCategory.TECHNICAL_QUESTION]}",
                "Prepare technical project clarification draft for student review."
            )

        if ReplyCategory.INTEREST in scores:
            return (
                ReplyCategory.INTEREST, 0.85,
                f"Positive research interest detected: {matches[ReplyCategory.INTEREST]}",
                "High Priority: Prepare tailored response draft for student review."
            )

        # Default fallback
        return (
            "INFO_REQUEST", 0.50,
            "General incoming correspondence requiring human review.",
            "Present to human operator for assessment."
        )


# Global reply classifier instance
reply_classifier = ReplyClassifier()
