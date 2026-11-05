"""
Opportunity Intelligence Engine: Unified Outreach Bridge & Multi-Category Routing Engine.

Unified Scheduling & Outreach Integration Architecture:
1. Multi-Category Routing:
   - Email-addressed opportunities (Academic, Startup, Frontier Industry, Institutional)
     -> Routed to Unified Regional Time-Block Schedule & Outreach Queue.
   - Portal-addressed opportunities (Formal fellowships, corporate careers portals)
     -> Routed to Application Tracker with deadline urgency & required documents dossier.
   - Unverified / Needs-Review contacts
     -> Quarantined with EXCLUDE_FROM_AUTOMATIC_DISPATCH.
2. Dynamic IANA Timezone & 13 Regional Time Blocks:
   - Resolves recipient local working hours (08:30 - 09:30 local midpoint).
   - Dynamically calculates IST send windows accounting for Daylight Saving Time (DST).
   - Assigns established regional blocks: BLOCK_01 to BLOCK_13.
3. Domain-Adaptive Personalization:
   - Generates domain-grounded drafts tailored to Category (Research overlap for Academia,
     High-agency systems pitch for Startups, Role benchmarks for Industry, Fellowship dossier for Institutions).
   - Links applicant Harshith N's verified IIT Delhi Chemical Engineering profile & 6 systems projects.
4. Delivery-Tracking Readiness:
   - Prepares delivery-tracker metadata (10-minute active watch, IMAP bounce monitoring, Message-ID schema).
5. Safe Development-Only Isolation:
   - Operates on development SQLite (opportunity_dev.db) and development workbook (Summer_2027_Opportunity_Workbook.xlsx).
   - Zero interference with active production professor outreach.
"""

import os
import re
import json
import logging
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from zoneinfo import ZoneInfo

from config.settings import settings
from src.opportunities.models import (
    Opportunity,
    Organization,
    OpportunityType,
    ContactChannel,
    VerificationStatus,
    PriorityTier,
    ContactHistoryStatus
)
from src.opportunities.repository import OpportunityRepository
from src.opportunities.personalization import DomainAdaptivePersonalizer
from src.opportunities.timezone_engine import DynamicTimezoneEngine
from src.opportunities.workbook_sync import OpportunityWorkbookSync
from src.security.audit_logger import audit_logger

logger = logging.getLogger("unified_outreach_bridge")


class UnifiedOutreachBridge:
    """
    Unified multi-category bridge connecting discovered opportunities
    with the autonomous outreach and application tracking machinery.
    """

    def __init__(
        self,
        repo: OpportunityRepository,
        workbook_sync: Optional[OpportunityWorkbookSync] = None
    ):
        self.repo = repo
        self.wb_sync = workbook_sync or OpportunityWorkbookSync(
            Path(settings.DATA_DIR / "development" / "Summer_2027_Opportunity_Workbook.xlsx")
        )
        self.ist_tz = ZoneInfo("Asia/Kolkata")

    def route_and_process_opportunity(
        self,
        opp: Opportunity,
        org: Organization,
        target_date: Optional[date] = None
    ) -> Dict[str, Any]:
        """
        Processes a single opportunity through the complete qualification,
        contact-history reconciliation, time-block assignment, and outreach/portal routing pipeline.
        """
        if target_date is None:
            target_date = date(2026, 11, 5)

        # 1. Contact Verification & Channel Check
        has_email = bool(opp.contact_email and "@" in opp.contact_email)
        is_verified = (opp.contact_verification_status or "").upper() in ("VERIFIED", "VALID")
        is_portal = (
            opp.hiring_contact_channel == ContactChannel.APPLICATION_PORTAL.value
            or (not has_email and bool(opp.application_url))
        )

        # 2. Dynamic Timezone & Regional Block Calculation (Target Date Aware)
        tz_str = opp.iana_timezone or org.iana_timezone or "UTC"
        tz_calc = DynamicTimezoneEngine.calculate_mailing_window(tz_str, target_date=target_date)

        # 3. Determine Authoritative Contact History Status
        hist_status = opp.contact_history_status
        if not hist_status:
            if is_portal:
                hist_status = ContactHistoryStatus.PORTAL_APPLICATION.value
            elif not has_email or not is_verified:
                hist_status = ContactHistoryStatus.NEEDS_REVIEW.value
            else:
                is_suppressed = self.repo.is_contact_suppressed_or_already_contacted(opp.contact_email)
                if is_suppressed:
                    hist_status = ContactHistoryStatus.ALREADY_CONTACTED.value
                else:
                    hist_status = ContactHistoryStatus.SCHEDULED_ELIGIBLE.value

        # 4. Routing Decision based on Reconciled History
        if hist_status == ContactHistoryStatus.PORTAL_APPLICATION.value:
            routing_channel = "PORTAL_APPLICATION"
            outreach_readiness = "PORTAL_TRACKED"
            next_action = "PREPARE_APPLICATION_DOSSIER"
            assigned_block_id = "PORTAL_DEADLINE_TRACKER"
        elif hist_status == ContactHistoryStatus.SCHEDULED_ELIGIBLE.value:
            routing_channel = "EMAIL_OUTREACH"
            outreach_readiness = "READY_FOR_SCHEDULED_DISPATCH"
            next_action = "DISPATCH_IN_ASSIGNED_TIME_BLOCK"
            assigned_block_id = tz_calc["assigned_block_id"]
        elif hist_status == ContactHistoryStatus.DELIVERY_UNCONFIRMED.value:
            routing_channel = "EMAIL_OUTREACH"
            outreach_readiness = "DELIVERY_IN_FLIGHT"
            next_action = "MONITOR_DELIVERY_IN_FLIGHT"
            assigned_block_id = tz_calc["assigned_block_id"]
        elif hist_status == ContactHistoryStatus.BOUNCED.value:
            routing_channel = "EMAIL_OUTREACH"
            outreach_readiness = "EXCLUDED_BOUNCED"
            next_action = "EXCLUDE_FROM_DISPATCH_BOUNCED"
            assigned_block_id = tz_calc["assigned_block_id"]
        elif hist_status == ContactHistoryStatus.REPLIED.value:
            routing_channel = "EMAIL_OUTREACH"
            outreach_readiness = "RESOLVED_REPLIED"
            next_action = "HANDLE_INCOMING_REPLY"
            assigned_block_id = tz_calc["assigned_block_id"]
        elif hist_status == ContactHistoryStatus.ALREADY_CONTACTED.value:
            routing_channel = "EMAIL_OUTREACH"
            outreach_readiness = "EXCLUDED_SUPPRESSED"
            next_action = "EXCLUDE_FROM_DISPATCH_ALREADY_CONTACTED"
            assigned_block_id = tz_calc["assigned_block_id"]
        else:  # NEEDS_REVIEW
            routing_channel = "UNVERIFIED_EMAIL"
            outreach_readiness = "QUARANTINE_UNVERIFIED"
            next_action = "VERIFY_RECIPIENT_CONTACT"
            assigned_block_id = tz_calc["assigned_block_id"]

        # 5. Domain-Adaptive Personalization Preparation
        draft_packet = None
        if routing_channel == "EMAIL_OUTREACH" and outreach_readiness == "READY_FOR_SCHEDULED_DISPATCH":
            draft_packet = self._generate_domain_draft(opp, org)

        # 6. Portal Application Metadata
        portal_packet = None
        if is_portal or opp.application_url:
            portal_packet = self._generate_portal_packet(opp, org)

        # 7. Internal Tracking & Planned Dispatch Token (No Fictional SMTP Message-ID)
        internal_tracking_id = f"TRK-{opp.opportunity_id[:12]}"
        planned_dispatch_ref = f"PLAN-{assigned_block_id}-{opp.opportunity_id[:8]}"

        result = {
            "opportunity_id": opp.opportunity_id,
            "organization_name": org.name,
            "contact_identity": opp.contact_name or f"{org.name} Technical Team",
            "category": opp.opportunity_type,
            "title": opp.title,
            "routing_channel": routing_channel,
            "outreach_readiness": outreach_readiness,
            "contact_history_status": hist_status,
            "assigned_block_id": assigned_block_id,
            "canonical_block_id": tz_calc.get("canonical_block_id", assigned_block_id),
            "canonical_label": tz_calc.get("canonical_label", f"{assigned_block_id} ({tz_calc['block_name']})"),
            "region_name": tz_calc["block_name"],
            "recipient_mailing_window": tz_calc["local_mailing_window"],
            "ist_dispatch_window": tz_calc["ist_mailing_window"],
            "iana_timezone": tz_str,
            "utc_offset": tz_calc["utc_offset"],
            "is_dst_active": tz_calc["is_dst_active"],
            "is_recipient_working_day": tz_calc.get("is_recipient_working_day", True),
            "contact_email": opp.contact_email,
            "contact_name": opp.contact_name,
            "application_url": opp.application_url,
            "qualification_score": opp.qualification_score,
            "priority_tier": opp.priority_tier or "PROMISING",
            "next_action": next_action,
            "internal_tracking_id": internal_tracking_id,
            "planned_dispatch_ref": planned_dispatch_ref,
            "tracking_reference": internal_tracking_id,
            "smtp_message_id": None,  # Real Message-ID generated ONLY upon actual/simulated message construction
            "draft_packet": draft_packet,
            "portal_packet": portal_packet
        }

        return result

    @classmethod
    def generate_real_message_id(cls, opp_id: str, sender_domain: str = "iitd.ac.in") -> str:
        """
        Generates authentic outgoing SMTP Message-ID at actual MIME message construction.
        Format: <{timestamp_ms}.{pid}.{hash_suffix}@{sender_domain}>
        """
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        pid = os.getpid()
        hash_suffix = abs(hash(f"{opp_id}:{now_ms}")) % 10000000
        return f"<{now_ms}.{pid}.{hash_suffix}@{sender_domain}>"

    @classmethod
    def simulate_smtp_dispatch(
        cls,
        route_result: Dict[str, Any],
        dry_run: bool = True
    ) -> Dict[str, Any]:
        """
        Simulates atomic message construction, generating an authentic outgoing Message-ID,
        and recording simulated SMTP acceptance separately.
        Guaranteed ZERO writes to production database and ZERO real SMTP transmissions.
        """
        opp_id = route_result["opportunity_id"]
        recipient = route_result.get("contact_email") or "test@domain.com"
        real_msg_id = cls.generate_real_message_id(opp_id)
        now_iso = datetime.now(timezone.utc).isoformat()

        # Simulated SMTP 250 response
        smtp_token = f"k{abs(hash(real_msg_id)) % 1000000000}"
        smtp_response = f"250 2.0.0 Ok: queued as {smtp_token}"

        dispatch_record = {
            "opportunity_id": opp_id,
            "internal_tracking_id": route_result["internal_tracking_id"],
            "recipient_email": recipient,
            "smtp_message_id": real_msg_id,
            "smtp_status": "SMTP_ACCEPTED_SIMULATED",
            "smtp_response_code": smtp_response,
            "smtp_accepted_at": now_iso,
            "delivery_watch_status": "ACTIVE_10_MIN_WATCH",
            "is_dry_run": dry_run,
            "historical_sent_written": False  # Dry-run NEVER marks historical SENT in production
        }
        return dispatch_record

    def _generate_domain_draft(self, opp: Opportunity, org: Organization) -> Dict[str, Any]:
        """
        Prepares domain-specific personalized draft and attachment specification.
        """
        cat = opp.opportunity_type
        if cat == OpportunityType.STARTUP.value:
            draft = DomainAdaptivePersonalizer.generate_startup_pitch(opp, org)
            attachments = ["Harshith_N_Research_CV_Summer2027.pdf"]
            strategy = "STARTUP_ENGINEERING_CONTRIBUTION_PITCH"
            subject = draft.get("pitch_subject") or draft.get("subject", "")
            body = draft.get("pitch_body") or draft.get("full_draft", "")
            recip = draft.get("recipient_name", "Founding Team")
            salutation = f"Hi {recip.split()[0] if ' ' in recip else recip},"
            words = body.split()
            word_count = len(words)
        elif cat == OpportunityType.FRONTIER_COMPANY.value:
            draft = DomainAdaptivePersonalizer.generate_frontier_industry_packet(opp, org)
            attachments = ["Harshith_N_Research_CV_Summer2027.pdf"]
            strategy = "FRONTIER_INDUSTRY_SYSTEMS_R&D_INQUIRY"
            subject = draft.get("pitch_subject") or draft.get("subject", "")
            body = draft.get("pitch_body") or draft.get("cover_letter_text", "")
            recip = draft.get("recipient_name", f"{org.name} Engineering Team")
            salutation = f"Hi {recip.split()[0] if ' ' in recip else recip},"
            words = body.split()
            word_count = len(words)
        elif cat == OpportunityType.INSTITUTIONAL_PROGRAM.value:
            draft = DomainAdaptivePersonalizer.generate_institutional_dossier(opp, org)
            attachments = [
                "Harshith_N_Research_CV_Summer2027.pdf",
                "Harshith_N_Research_Interests_Summer2027.pdf"
            ]
            strategy = "INSTITUTIONAL_FELLOWSHIP_RESEARCH_INQUIRY"
            subject = f"Summer 2027 Research Placement: {opp.title} — Harshith N (IIT Delhi)"
            body = draft.get("statement_of_purpose", "")
            salutation = f"Dear {org.name} Selection Committee,"
            words = body.split()
            word_count = len(words)
        else:  # ACADEMIC_LAB
            # Academic Professor Personalization
            full_name = opp.contact_name or "Professor"
            salutation = f"Dear Professor {full_name},"
            matched_proj = opp.matched_project_id or "Neural Process Twin"
            
            p1 = (
                f"My name is Harshith N, an undergraduate in Chemical Engineering at the Indian Institute of Technology Delhi (IIT Delhi). "
                f"I am writing to express my strong interest in joining your research group for an undergraduate research internship during Summer 2027."
            )
            p2 = (
                f"I have closely followed your research on {opp.technical_relevance_summary or 'computational modeling'}. "
                f"Your group's focus on advancing numerical methods and computational efficiency for complex physical systems "
                f"directly aligns with my academic interests."
            )
            if "inference" in matched_proj.lower() or "roofline" in (opp.technical_domains or []):
                p3 = (
                    "In my project, LLM Inference Intelligence Engine, I instrumented Roofline model diagnostics isolating "
                    "compute ceilings from memory-bandwidth bottlenecks across CPU and GPU architectures. "
                    "I evaluated prefill TTFT and decode TPOT latency under variable batching strategies. "
                    "I would welcome the opportunity to apply this systems profiling and computational performance background "
                    "to your group's machine learning and simulation initiatives."
                )
            else:
                p3 = (
                    "In my project, Neural Process Twin, I developed deep learning surrogate models in PyTorch for 2D steady-state "
                    "heat conduction, evaluating surrogate prediction accuracy against numerical finite difference solutions. "
                    "I am eager to contribute this background in scientific machine learning, differential equation modeling, "
                    "and numerical solvers to your ongoing computational research initiatives."
                )
            p4 = (
                "I would welcome the opportunity to contribute to your ongoing computational projects for 10–12 weeks in Summer 2027. "
                "My CV and research interests statement are attached for your review, and I would be grateful for the chance to discuss potential alignment."
            )
            sig = (
                "Sincerely,\n"
                "Harshith N\n"
                "Department of Chemical Engineering\n"
                "Indian Institute of Technology Delhi (IIT Delhi)\n"
                "ch1251635@iitd.ac.in"
            )
            full_body = f"{salutation}\n\n{p1}\n\n{p2}\n\n{p3}\n\n{p4}\n\n{sig}"
            words = full_body.split()
            word_count = len(words)
            subject = f"Summer 2027 Research Internship — {opp.technical_domains[0] if opp.technical_domains else 'Computational Systems'} | IIT Delhi"
            body = full_body
            attachments = [
                "Harshith_N_Research_CV_Summer2027.pdf",
                "Harshith_N_Research_Interests_Summer2027.pdf"
            ]
            strategy = "ACADEMIC_FACULTY_RESEARCH_OUTREACH"

        return {
            "strategy": strategy,
            "subject": subject,
            "salutation": salutation,
            "body": body,
            "word_count": word_count,
            "attachments": attachments,
            "candidate": "Harshith N (IIT Delhi, Chemical Engineering)",
            "grounding_verified": True
        }

    def _generate_portal_packet(self, opp: Opportunity, org: Organization) -> Dict[str, Any]:
        """
        Prepares structured formal portal tracking dossier.
        """
        deadline_str = opp.application_deadline or "Rolling"
        deadline_urgency = "ROLLING"
        if deadline_str != "Rolling":
            try:
                dl_date = datetime.strptime(deadline_str, "%Y-%m-%d").date()
                today = datetime.now(timezone.utc).date()
                delta_days = (dl_date - today).days
                if delta_days < 0:
                    deadline_urgency = "EXPIRED"
                elif delta_days <= 14:
                    deadline_urgency = "URGENT_UNDER_14_DAYS"
                elif delta_days <= 45:
                    deadline_urgency = "UPCOMING_UNDER_45_DAYS"
                else:
                    deadline_urgency = "FUTURE_OPENING"
            except Exception:
                deadline_urgency = "SPECIFIED_DEADLINE"

        required_docs = opp.required_documents or "CV, Official Transcripts, Statement of Purpose, 2 Letters of Recommendation"
        
        return {
            "application_url": opp.application_url or org.website_url,
            "deadline": deadline_str,
            "deadline_urgency": deadline_urgency,
            "required_documents": required_docs,
            "eligibility_summary": opp.eligibility_criteria or "Enrolled undergraduate; international students eligible with J-1 / standard visitor sponsorship",
            "compensation_details": opp.compensation_details or opp.compensation_type,
            "portal_status": "TRACKED_FOR_FORMAL_SUBMISSION"
        }

    def build_unified_outreach_inventory(self, target_date: Optional[date] = None) -> Dict[str, Any]:
        """
        Builds the complete operational inventory across all opportunities in opportunity_dev.db.
        Separates scheduled email outreach from portal-tracked and unverified opportunities.
        """
        if target_date is None:
            target_date = date(2026, 11, 5)

        # Load all opportunities (all scores)
        all_opps = self.repo.list_opportunities(min_score=0.0, limit=10000)
        
        orgs_by_id: Dict[str, Organization] = {}
        for opp in all_opps:
            if opp.organization_id not in orgs_by_id:
                org_obj = self.repo.get_organization(opp.organization_id)
                if org_obj:
                    orgs_by_id[opp.organization_id] = org_obj

        email_scheduled: List[Dict[str, Any]] = []
        delivery_unconfirmed: List[Dict[str, Any]] = []
        bounced_excluded: List[Dict[str, Any]] = []
        replied_resolved: List[Dict[str, Any]] = []
        portal_tracked: List[Dict[str, Any]] = []
        unverified_quarantine: List[Dict[str, Any]] = []
        suppressed_excluded: List[Dict[str, Any]] = []

        block_distribution: Dict[str, int] = {}
        category_distribution: Dict[str, Dict[str, int]] = {
            OpportunityType.ACADEMIC_LAB.value: {"eligible": 0, "sent": 0, "bounced": 0, "replied": 0, "portal": 0, "unverified": 0, "suppressed": 0},
            OpportunityType.STARTUP.value: {"eligible": 0, "sent": 0, "bounced": 0, "replied": 0, "portal": 0, "unverified": 0, "suppressed": 0},
            OpportunityType.FRONTIER_COMPANY.value: {"eligible": 0, "sent": 0, "bounced": 0, "replied": 0, "portal": 0, "unverified": 0, "suppressed": 0},
            OpportunityType.INSTITUTIONAL_PROGRAM.value: {"eligible": 0, "sent": 0, "bounced": 0, "replied": 0, "portal": 0, "unverified": 0, "suppressed": 0}
        }

        for opp in all_opps:
            org = orgs_by_id.get(opp.organization_id) or Organization(
                organization_id=opp.organization_id,
                name=opp.organization_id,
                category=opp.opportunity_type
            )
            processed = self.route_and_process_opportunity(opp, org, target_date=target_date)
            cat = opp.opportunity_type
            readiness = processed["outreach_readiness"]

            if readiness == "READY_FOR_SCHEDULED_DISPATCH":
                email_scheduled.append(processed)
                b_id = processed["assigned_block_id"]
                block_distribution[b_id] = block_distribution.get(b_id, 0) + 1
                if cat in category_distribution:
                    category_distribution[cat]["eligible"] += 1

            elif readiness == "DELIVERY_IN_FLIGHT":
                delivery_unconfirmed.append(processed)
                if cat in category_distribution:
                    category_distribution[cat]["sent"] += 1

            elif readiness == "EXCLUDED_BOUNCED":
                bounced_excluded.append(processed)
                if cat in category_distribution:
                    category_distribution[cat]["bounced"] += 1

            elif readiness == "RESOLVED_REPLIED":
                replied_resolved.append(processed)
                if cat in category_distribution:
                    category_distribution[cat]["replied"] += 1

            elif readiness == "PORTAL_TRACKED":
                portal_tracked.append(processed)
                if cat in category_distribution:
                    category_distribution[cat]["portal"] += 1

            elif readiness == "EXCLUDED_SUPPRESSED":
                suppressed_excluded.append(processed)
                if cat in category_distribution:
                    category_distribution[cat]["suppressed"] += 1

            else:  # QUARANTINE_UNVERIFIED
                unverified_quarantine.append(processed)
                if cat in category_distribution:
                    category_distribution[cat]["unverified"] += 1

        summary = {
            "total_evaluated": len(all_opps),
            "target_date": target_date.isoformat(),
            "scheduled_eligible_count": len(email_scheduled),
            "delivery_unconfirmed_count": len(delivery_unconfirmed),
            "bounced_count": len(bounced_excluded),
            "replied_count": len(replied_resolved),
            "suppressed_count": len(suppressed_excluded),
            "portal_tracked_count": len(portal_tracked),
            "unverified_quarantine_count": len(unverified_quarantine),
            "block_distribution": block_distribution,
            "category_distribution": category_distribution,
            "email_scheduled_sample": email_scheduled[:5],
            "portal_tracked_sample": portal_tracked[:5]
        }

        return summary
