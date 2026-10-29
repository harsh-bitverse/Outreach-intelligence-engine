"""
Contextual Reply and Follow-Up Drafting Engine.
Generates draft responses strictly for human review and approval.
Does NOT expose or contain any transmission capabilities.
"""

import hashlib
from typing import Dict, Any, Optional
from src.database.models import ReplyDraftRecord, FollowUpDraftRecord
from src.intelligence.knowledge_base import knowledge_base
from src.intelligence.reply_classifier import ReplyCategory


class ReplyDrafter:
    """Generates context-aware reply drafts based on incoming messages and verified student data."""

    def __init__(self, kb=None):
        self.kb = kb or knowledge_base

    def draft_reply(
        self,
        professor: Dict[str, Any],
        incoming_message: Dict[str, Any],
        classification: Dict[str, Any]
    ) -> Optional[ReplyDraftRecord]:
        """
        Draft a contextual response strictly for human operator review.
        Returns None for categories where no response is warranted (REJECTION, BOUNCE, OPT_OUT).
        """
        category = classification.get("category")
        if category in (ReplyCategory.BOUNCE, ReplyCategory.OPT_OUT, ReplyCategory.REJECTION):
            # No reply should ever be drafted or sent for rejections, bounces, or opt-outs.
            return None

        full_name = professor.get("full_name", "").strip()
        last_name = full_name.split()[-1] if full_name else "Professor"
        salutation = f"Dear Prof. {last_name},"

        signature = (
            "Sincerely,\n"
            "Harshith N\n"
            "Undergraduate, Department of Chemical Engineering\n"
            "Indian Institute of Technology Delhi (IIT Delhi)\n"
            "ch1251635@iitd.ac.in"
        )

        body_content = ""

        if category == ReplyCategory.MEETING_INVITE:
            body_content = (
                f"Thank you very much for your encouraging reply and the invitation to speak. "
                f"I would be glad to meet with you virtually to discuss potential Summer 2027 research opportunities.\n\n"
                f"I am flexible to coordinate around your time zone and can make myself available during your preferred hours. "
                f"Please let me know a convenient date, time, and video platform (Google Meet / Zoom / Teams) that works best for your schedule."
            )

        elif category == ReplyCategory.INFO_REQUEST:
            body_content = (
                f"Thank you for your response. In accordance with your request, I have attached my latest "
                f"comprehensive CV detailing my coursework, academic milestones at IIT Delhi, and verified research projects.\n\n"
                f"Should you require any additional materials, such as course transcripts or code documentation from my "
                f"SciML and systems work, I would be pleased to share them immediately."
            )

        elif category == ReplyCategory.REFERRAL:
            body_content = (
                f"Thank you for directing me to this relevant information. I appreciate your guidance and "
                f"will follow up accordingly with the suggested contact and departmental application procedures.\n\n"
                f"Thank you again for your time and assistance."
            )

        elif category == ReplyCategory.FUNDING_QUESTION:
            body_content = (
                f"Thank you for inquiring about funding. I am currently exploring institutional travel grants "
                f"and external scholarship avenues from IIT Delhi to support my Summer 2027 research stay.\n\n"
                f"I would be glad to discuss potential collaborative arrangements or departmental frameworks that support visiting research students."
            )

        elif category == ReplyCategory.TECHNICAL_QUESTION:
            body_content = (
                f"Thank you for your thoughtful technical inquiry. In my work on physics-aware surrogate modeling "
                f"(Neural Process Twin) and LLM inference benchmarking, I focused on rigorous empirical evaluation "
                f"and verifiable computational performance.\n\n"
                f"I would welcome the opportunity to discuss the specific algorithmic decisions, constraint formulations, "
                f"and implementation details in greater depth over a brief call."
            )

        else: # INTEREST / General positive
            body_content = (
                f"Thank you very much for your positive response and consideration of my inquiry.\n\n"
                f"I remain deeply interested in contributing to your group's computational research during Summer 2027. "
                f"I would welcome the chance to schedule a brief introductory conversation at your convenience to explore "
                f"potential project alignments."
            )

        draft_body = f"{salutation}\n\n{body_content}\n\n{signature}"
        words = draft_body.split()

        reply_draft_id = "rdraft_" + hashlib.sha256(
            f"{professor.get('professor_id', '')}_{incoming_message.get('message_id', '')}".encode()
        ).hexdigest()[:14]

        return ReplyDraftRecord(
            reply_draft_id=reply_draft_id,
            thread_id=incoming_message.get("thread_id", ""),
            reply_to_message_id=incoming_message.get("message_id", ""),
            professor_id=professor.get("professor_id", ""),
            draft_body=draft_body,
            word_count=len(words),
            status="AWAITING_HUMAN_REVIEW"
        )

    def draft_followup(
        self,
        professor: Dict[str, Any],
        original_message: Dict[str, Any],
        days_elapsed: int = 7
    ) -> FollowUpDraftRecord:
        """
        Draft a respectful, concise follow-up for an unanswered first contact.
        Strictly for human operator review and manual approval.
        """
        full_name = professor.get("full_name", "").strip()
        last_name = full_name.split()[-1] if full_name else "Professor"
        salutation = f"Dear Prof. {last_name},"

        signature = (
            "Sincerely,\n"
            "Harshith N\n"
            "Undergraduate, Department of Chemical Engineering\n"
            "Indian Institute of Technology Delhi (IIT Delhi)\n"
            "ch1251635@iitd.ac.in"
        )

        body = (
            f"{salutation}\n\n"
            f"I hope you are having a productive week. I am following up on my previous note regarding prospective "
            f"research internship opportunities in your group for Summer 2027.\n\n"
            f"I understand that you have a demanding schedule and receive many inquiries. If your lab is considering "
            f"visiting undergraduate researchers for computational or modeling projects next summer, I would be "
            f"delighted to discuss how my background at IIT Delhi aligns with your team's objectives.\n\n"
            f"My CV remains attached for your convenience, and I thank you again for your time and consideration.\n\n"
            f"{signature}"
        )

        followup_id = "fdraft_" + hashlib.sha256(
            f"{professor.get('professor_id', '')}_{original_message.get('message_id', '')}".encode()
        ).hexdigest()[:14]

        return FollowUpDraftRecord(
            followup_draft_id=followup_id,
            professor_id=professor.get("professor_id", ""),
            original_message_id=original_message.get("message_id", ""),
            draft_body=body,
            word_count=len(body.split()),
            scheduled_after_days=days_elapsed,
            status="AWAITING_HUMAN_REVIEW"
        )


# Global reply drafter instance
reply_drafter = ReplyDrafter()
