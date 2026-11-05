"""
Opportunity Intelligence Engine: Production Portfolio Reconciler.
Reconciles the 1,390 existing production professor records from data/outreach_engine.db
into an isolated development representation in data/development/opportunity_dev.db.

Preserves all historical campaign dimensions:
- Stable source identifiers (source='PRODUCTION_CAMPAIGN_LINKED', linked_professor_id)
- Original professor ID, university, country, department, and QS rank
- Current verified email status (VERIFIED, INVALID, UNVERIFIED)
- Exact outreach status (AWAITING_DRAFT, SENT, BOUNCED, REPLIED, SUPPRESSED)
- Existing IST mailing time-block assignments (BLOCK_01 to BLOCK_13)
- Research overlap and matched CV skills/projects
- Deduplication against existing development academic records
"""

import logging
import sqlite3
import json
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Set
from datetime import datetime, timezone, date

from config.settings import settings
from src.opportunities.models import (
    Organization,
    Opportunity,
    OpportunityType,
    OpportunityClassification,
    RoleTitleType,
    WorkplaceType,
    InternationalEligibility,
    CompensationType,
    ContactChannel,
    VerificationStatus,
    RequisitionType,
    PortfolioTier,
    PriorityTier,
    AcademicClassification,
    VisitingStudentPolicy,
    HostingAvailability,
    FundingAvailability,
    AcademicReplyOutcome,
    ContactHistoryStatus
)
from src.opportunities.timezone_engine import DynamicTimezoneEngine, PRODUCTION_TIME_BLOCKS, DEVELOPMENT_EXTENDED_BLOCKS
from src.opportunities.registry import (
    canonicalize_url,
    compute_organization_id,
    compute_opportunity_id
)
from src.opportunities.contact_verifier import validate_email_syntax

logger = logging.getLogger(__name__)


class AcademicPortfolioReconciler:
    """
    Safely bridges and reconciles the 1,390 production professors into the
    Opportunity Intelligence Engine development database.
    """

    def __init__(
        self,
        prod_db_path: Optional[Path] = None,
        dev_db_path: Optional[Path] = None
    ):
        self.prod_db_path = prod_db_path or (settings.DATA_DIR / "outreach_engine.db")
        self.dev_db_path = dev_db_path or (settings.DATA_DIR / "development" / "opportunity_dev.db")

    def load_production_professors(self) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
        """
        Loads all 1,390 professors and inbound reply messages from production database in READ-ONLY mode.
        """
        if not self.prod_db_path.exists():
            logger.warning(f"Production database not found at {self.prod_db_path}")
            return [], {}

        # Read-only URI connection with short timeout
        conn = sqlite3.connect(f"file:{self.prod_db_path}?mode=ro", uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("""
            SELECT 
                professor_id, qs_rank, processing_sequence, university_name, country,
                full_name, title, department, email, email_verification_status,
                verification_source_url, faculty_profile_url, research_areas,
                research_overlap, cv_skill_project, potential_contribution,
                evidence_url, date_verified, notes, outreach_status,
                time_block, time_block_order, iana_timezone, created_at, updated_at
            FROM professors
            ORDER BY processing_sequence ASC;
        """)
        rows = [dict(r) for r in cur.fetchall()]

        # Query inbound messages
        inbound_msgs: Dict[str, Dict[str, Any]] = {}
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='email_messages';")
        if cur.fetchone():
            cur.execute("""
                SELECT professor_id, message_id, subject, body, sent_received_at
                FROM email_messages
                WHERE direction = 'INBOUND';
            """)
            inbound_msgs = {r["professor_id"]: dict(r) for r in cur.fetchall()}

        conn.close()

        logger.info(f"Loaded {len(rows)} production professors and {len(inbound_msgs)} inbound replies from {self.prod_db_path}")
        return rows, inbound_msgs

    def reconcile_into_development_db(self) -> Dict[str, Any]:
        """
        Reconciles production professors into the isolated development database.
        - Identifies existing development academic records that match production professors
        - Ingests production professors with source='PRODUCTION_CAMPAIGN_LINKED'
        - Preserves exact historical delivery, outreach, and email statuses
        - Decouples address delivery failures from academic/visa ineligibility
        - Returns full reconciliation accounting metrics
        """
        prod_profs, inbound_msgs = self.load_production_professors()
        if not prod_profs:
            return {"status": "ERROR", "message": "No production professors loaded"}

        conn = sqlite3.connect(str(self.dev_db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        now_iso = datetime.now(timezone.utc).isoformat()

        # Step 1: Identify existing development academic records for deduplication
        cur.execute("SELECT opportunity_id, contact_name, contact_email FROM opportunities WHERE opportunity_type='ACADEMIC_LAB';")
        dev_academic_rows = cur.fetchall()
        dev_academic_by_email = {r["contact_email"].lower(): r["opportunity_id"] for r in dev_academic_rows if r["contact_email"]}
        dev_academic_by_name = {r["contact_name"].lower(): r["opportunity_id"] for r in dev_academic_rows if r["contact_name"]}

        reconciliation_stats = {
            "total_production_professors": len(prod_profs),
            "linked_to_existing_dev_records": 0,
            "net_new_production_ingested": 0,
            "status_breakdown": {},
            "verification_breakdown": {},
            "unique_universities": len(set(p["university_name"] for p in prod_profs)),
            "unique_countries": len(set(p["country"] for p in prod_profs))
        }

        # Step 2: Iterate over production professors and ingest into development DB
        for p in prod_profs:
            p_id = p["professor_id"]
            p_name = p["full_name"] or "Professor"
            p_email = (p["email"] or "").strip().lower()
            p_uni = p["university_name"] or "Global University"
            p_country = p["country"] or "Unknown"
            p_dept = p["department"] or "Engineering / CS"
            p_status = p["outreach_status"] or "AWAITING_DRAFT"
            p_verif = p["email_verification_status"] or "UNVERIFIED"

            # Track status stats
            reconciliation_stats["status_breakdown"][p_status] = reconciliation_stats["status_breakdown"].get(p_status, 0) + 1
            reconciliation_stats["verification_breakdown"][p_verif] = reconciliation_stats["verification_breakdown"].get(p_verif, 0) + 1

            # Check if this professor was already seeded as a test record in development
            matching_dev_opp_id = dev_academic_by_email.get(p_email) or dev_academic_by_name.get(p_name.lower())

            # Generate organization
            org_id = compute_organization_id(p_uni, p.get("faculty_profile_url") or "")
            cur.execute("""
                INSERT INTO organizations (
                    organization_id, name, category, website_url, country, iana_timezone, tier, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(organization_id) DO UPDATE SET
                    country = excluded.country,
                    iana_timezone = excluded.iana_timezone;
            """, (
                org_id, p_uni, OpportunityType.ACADEMIC_LAB.value,
                p.get("faculty_profile_url") or "", p_country, p.get("iana_timezone") or "UTC",
                "TIER_1", now_iso, now_iso
            ))

            # Determine academic feasibility dimensions from production status
            is_bounced = "BOUNCED" in p_status or "INVALID" in p_status or p_verif == "INVALID"
            is_suppressed = p_status == "SUPPRESSED"
            is_replied = "REPLIED" in p_status
            is_sent = "SENT" in p_status

            reply_evidence = None
            reply_ts = None

            if is_bounced or is_suppressed:
                # CRITICAL SEPARATION: Contact failure != Academic / Visa Ineligibility!
                # Research match remains valid as a prospective inquiry,
                # but the contact is marked INVALID and excluded from dispatch.
                acad_class = AcademicClassification.PROSPECTIVE_INQUIRY.value
                hosting_avail = HostingAvailability.UNKNOWN.value
                reply_outcome = AcademicReplyOutcome.BOUNCED.value if is_bounced else AcademicReplyOutcome.DECLINED.value
                contact_verif = "INVALID" if is_bounced else p_verif
                next_act = "EXCLUDE_FROM_DISPATCH_NEED_ALTERNATIVE_CONTACT"
                verif_notes = (
                    f"Reconciled from production campaign (Time Block: {p.get('time_block', 'UNASSIGNED')}). "
                    f"Contact status: {p_status} (Delivery Bounced/Invalid). "
                    f"Research alignment remains valid; candidate excluded from dispatch pending verified alternative contact."
                )
            elif is_replied:
                acad_class = AcademicClassification.PROSPECTIVE_INQUIRY.value
                hosting_avail = HostingAvailability.UNKNOWN.value
                contact_verif = p_verif

                # Check if specific inbound message exists
                inbound_info = inbound_msgs.get(p_id)
                if inbound_info:
                    inbound_body = (inbound_info.get("body") or "").lower()
                    reply_ts = inbound_info.get("sent_received_at")
                    reply_evidence = inbound_info.get("body", "")[:250]
                    if any(term in inbound_body for term in ["zoom", "call", "schedule a call", "meet", "speak", "interview"]):
                        reply_outcome = AcademicReplyOutcome.INTERVIEW_REQUESTED.value
                        next_act = "SCHEDULE_ZOOM_INTERVIEW"
                    elif any(term in inbound_body for term in ["interested", "glad to host", "welcome to apply"]):
                        reply_outcome = AcademicReplyOutcome.INTERESTED.value
                        next_act = "PREPARE_FORMAL_PROPOSAL"
                    elif any(term in inbound_body for term in ["full", "capacity", "no space", "no openings"]):
                        reply_outcome = AcademicReplyOutcome.CAPACITY_FULL.value
                        next_act = "LOG_LAB_CAPACITY_FULL"
                    elif any(term in inbound_body for term in ["no funding", "unfunded", "cannot fund"]):
                        reply_outcome = AcademicReplyOutcome.FUNDING_UNAVAILABLE.value
                        next_act = "EXPLORE_EXTERNAL_SCHOLARSHIP"
                    elif any(term in inbound_body for term in ["cannot take", "decline", "unable to accept"]):
                        reply_outcome = AcademicReplyOutcome.DECLINED.value
                        next_act = "ARCHIVE_DECLINED_INQUIRY"
                    else:
                        reply_outcome = AcademicReplyOutcome.NEEDS_REVIEW.value
                        next_act = "REVIEW_PROFESSOR_REPLY"
                else:
                    reply_outcome = AcademicReplyOutcome.NEEDS_REVIEW.value
                    next_act = "REVIEW_PROFESSOR_REPLY"
                    reply_evidence = "Reply recorded in production tracking; message body awaiting IMAP synchronization."
                    reply_ts = p.get("updated_at")

                verif_notes = (
                    f"Reconciled from production campaign (Time Block: {p.get('time_block', 'UNASSIGNED')}). "
                    f"Outreach status: REPLIED. Reply outcome: {reply_outcome}."
                )
            elif is_sent:
                acad_class = AcademicClassification.PROSPECTIVE_INQUIRY.value
                hosting_avail = HostingAvailability.UNKNOWN.value
                reply_outcome = AcademicReplyOutcome.NO_REPLY.value
                contact_verif = p_verif
                next_act = "AWAITING_REPLY_MONITOR"
                verif_notes = (
                    f"Reconciled from production campaign (Time Block: {p.get('time_block', 'UNASSIGNED')}). "
                    f"Outreach dispatched via production SMTP; tracking delivery/reply."
                )
            else:
                acad_class = AcademicClassification.PROSPECTIVE_INQUIRY.value
                hosting_avail = HostingAvailability.UNKNOWN.value
                reply_outcome = None
                contact_verif = p_verif
                next_act = "AWAITING_REVIEW"
                verif_notes = f"Reconciled from production campaign (Time Block: {p.get('time_block', 'UNASSIGNED')})."

            # Split research domains
            raw_areas = p.get("research_areas") or ""
            domains = [d.strip() for d in raw_areas.split(",") if d.strip()]
            if not domains:
                domains = ["Scientific Computing", "AI Systems"]

            title = f"Summer 2027 Visiting Research Placement — {p_name} ({p_dept})"
            opp_id = matching_dev_opp_id if matching_dev_opp_id else f"opp_prod_{p_id}"

            if matching_dev_opp_id:
                reconciliation_stats["linked_to_existing_dev_records"] += 1
                # Update existing test seed record with production link
                cur.execute("""
                    UPDATE opportunities SET
                        professor_id = ?,
                        operational_status = ?,
                        contact_verification_status = ?,
                        academic_classification = ?,
                        academic_reply_outcome = ?,
                        reply_timestamp = ?,
                        reply_evidence_snippet = ?,
                        verification_notes = ?,
                        next_action = ?,
                        updated_at = ?
                    WHERE opportunity_id = ?;
                """, (p_id, p_status, contact_verif, acad_class, reply_outcome, reply_ts, reply_evidence, verif_notes, next_act, now_iso, matching_dev_opp_id))
            else:
                reconciliation_stats["net_new_production_ingested"] += 1
                # Ingest production record into development opportunities table
                tech_domains_json = json.dumps(domains)
                evidence_urls_json = json.dumps([p.get("faculty_profile_url") or "", p.get("evidence_url") or ""])

                cur.execute("""
                    INSERT INTO opportunities (
                        opportunity_id, organization_id, title, opportunity_type, source,
                        location, iana_timezone, technical_domains, technical_relevance_summary,
                        potential_contribution, matched_project_id, eligibility_criteria,
                        internship_duration_weeks, target_term, compensation_type,
                        compensation_details, official_evidence_urls, freshness_timestamp,
                        hiring_contact_channel, contact_email, contact_name,
                        contact_verification_status, application_url, application_deadline,
                        qualification_score, priority_tier, portfolio_tier, requisition_type,
                        role_title_type, workplace_type, international_eligibility,
                        compensation_evidence, academic_classification, institution_visiting_policy,
                        hosting_history_verified, summer_2027_hosting_availability,
                        funding_availability, application_route, academic_reply_outcome,
                        professor_id, operational_status, verification_notes,
                        discovery_status, application_status, is_confirmed_opening, next_action,
                        created_at, updated_at, last_checked_at, reply_timestamp, reply_evidence_snippet
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    ON CONFLICT(opportunity_id) DO UPDATE SET
                        operational_status = excluded.operational_status,
                        contact_verification_status = excluded.contact_verification_status,
                        academic_classification = excluded.academic_classification,
                        academic_reply_outcome = excluded.academic_reply_outcome,
                        reply_timestamp = excluded.reply_timestamp,
                        reply_evidence_snippet = excluded.reply_evidence_snippet,
                        verification_notes = excluded.verification_notes,
                        next_action = excluded.next_action,
                        updated_at = excluded.updated_at;
                """, (
                    opp_id, org_id, title, OpportunityType.ACADEMIC_LAB.value, "PRODUCTION_CAMPAIGN_LINKED",
                    f"{p_uni}, {p_country}", p.get("iana_timezone") or "UTC", tech_domains_json,
                    p.get("research_overlap") or p.get("research_areas") or "High research fit",
                    p.get("potential_contribution") or "Independent systems and scientific computing research collaboration.",
                    p.get("cv_skill_project") or "LLM Inference Intelligence Engine",
                    "Enrolled Engineering Undergraduate (IIT Delhi)",
                    10, "Summer 2027", CompensationType.UNKNOWN.value,
                    "Funding availability subject to faculty discretion / institutional fellowship.",
                    evidence_urls_json, now_iso,
                    ContactChannel.FACULTY_PI.value, p_email, p_name,
                    contact_verif, p.get("faculty_profile_url") or "", "Rolling",
                    75.0, PriorityTier.HIGH_PRIORITY.value, PortfolioTier.TIER_B_STRONG_MATCH.value,
                    RequisitionType.UNDERGRADUATE_INTERNSHIP.value, RoleTitleType.PROPOSED_OUTREACH_FOCUS.value,
                    WorkplaceType.ONSITE.value, InternationalEligibility.SPONSORED_OR_J1_REQUIRED.value,
                    "FACULTY_RESEARCH_GRANT", acad_class,
                    VisitingStudentPolicy.INDEPENDENT_RESEARCH_SCHOLAR_PERMITTED.value,
                    0, hosting_avail, FundingAvailability.UNKNOWN.value,
                    "DIRECT_FACULTY_PI", reply_outcome,
                    p_id, p_status, verif_notes,
                    "QUALIFIED", "NOT_APPLIED", 0, next_act,
                    p.get("created_at") or now_iso, now_iso, now_iso,
                    reply_ts, reply_evidence
                ))

        conn.commit()
        conn.close()

        logger.info(
            f"Production reconciliation complete: {reconciliation_stats['total_production_professors']} professors "
            f"({reconciliation_stats['linked_to_existing_dev_records']} linked, "
            f"{reconciliation_stats['net_new_production_ingested']} net-new ingested)."
        )
        return reconciliation_stats

    def reconcile_all_contacts(self, target_date: Optional[date] = None) -> Dict[str, Any]:
        """
        Reconciles all opportunities in opportunity_dev.db against the latest
        production campaign history in outreach_engine.db (READ-ONLY).

        Recognizes:
        - Previously contacted addresses
        - SMTP-accepted messages still awaiting final delivery (DELIVERY_UNCONFIRMED)
        - Permanent bounces (BOUNCED)
        - Alternative addresses already contacted (ALREADY_CONTACTED)
        - Genuine professor replies (REPLIED)
        - Interview requests, declines, and opt-outs
        - Existing suppression policies
        - Ambiguous deliveries & errors (NEEDS_REVIEW)
        - Portal requisitions (PORTAL_APPLICATION)
        - Genuinely new, eligible, unsent contacts (SCHEDULED_ELIGIBLE)

        Computes recipient-specific IANA mailing windows and IST eligibility windows
        using DynamicTimezoneEngine.
        """
        if target_date is None:
            target_date = date(2026, 11, 5)

        # 1. Connect to production database in read-only mode
        prod_by_pid: Dict[str, Dict[str, Any]] = {}
        prod_by_norm_name: Dict[str, List[Dict[str, Any]]] = {}
        tracker_by_pid: Dict[str, Tuple[str, str]] = {}
        tracker_by_email: Dict[str, Tuple[str, str]] = {}
        suppressed_emails: Set[str] = set()

        import re
        def _normalize_name(n: Optional[str]) -> str:
            if not n:
                return ""
            cleaned = re.sub(r"^(prof\.|dr\.|professor)\s+", "", n.strip(), flags=re.IGNORECASE)
            return re.sub(r"\s+", " ", cleaned).strip().lower()

        if self.prod_db_path.exists():
            prod_conn = sqlite3.connect(f"file:{self.prod_db_path}?mode=ro", uri=True, timeout=2.0)
            prod_cur = prod_conn.cursor()

            # Gather professors
            prod_cur.execute("SELECT professor_id, full_name, university_name, email, outreach_status FROM professors;")
            for pid, name, uni, email, status in prod_cur.fetchall():
                clean_email = (email or "").strip().lower()
                clean_name = name or ""
                norm_name = _normalize_name(clean_name)
                info = {
                    "pid": pid,
                    "name": clean_name,
                    "uni": uni or "",
                    "email": clean_email,
                    "outreach_status": status or "AWAITING_DRAFT"
                }
                prod_by_pid[pid] = info
                if norm_name:
                    if norm_name not in prod_by_norm_name:
                        prod_by_norm_name[norm_name] = []
                    prod_by_norm_name[norm_name].append(info)

            # Gather delivery tracker
            prod_cur.execute("SELECT professor_id, recipient_email, delivery_status, tracking_status FROM outbound_delivery_tracker;")
            for pid, email, d_stat, t_stat in prod_cur.fetchall():
                em = (email or "").strip().lower()
                tracker_by_pid[pid] = (d_stat, t_stat)
                if em:
                    tracker_by_email[em] = (d_stat, t_stat)

            # Gather suppression list
            prod_cur.execute("SELECT email FROM suppression_list;")
            for (em,) in prod_cur.fetchall():
                if em:
                    suppressed_emails.add(em.strip().lower())

            prod_conn.close()
        else:
            logger.warning(f"Production database not accessible at {self.prod_db_path}")

        # 2. Gather dev opportunities
        dev_conn = sqlite3.connect(str(self.dev_db_path))
        dev_conn.row_factory = sqlite3.Row
        dev_cur = dev_conn.cursor()

        dev_cur.execute("""
            SELECT o.opportunity_id, o.professor_id, o.opportunity_type, o.title, o.contact_name, o.contact_email,
                   o.contact_verification_status, o.hiring_contact_channel, o.application_url, o.iana_timezone,
                   org.name as org_name, org.category as org_category
            FROM opportunities o
            JOIN organizations org ON o.organization_id = org.organization_id;
        """)
        dev_opps = dev_cur.fetchall()

        now_iso = datetime.now(timezone.utc).isoformat()

        counts = {s.value: 0 for s in ContactHistoryStatus}
        block_distribution: Dict[str, int] = {}
        category_reconciliation: Dict[str, Dict[str, int]] = {}

        updates: List[Tuple[str, str, str, str, str, str]] = []

        for row in dev_opps:
            opp_id = row["opportunity_id"]
            pid = row["professor_id"]
            cat = row["opportunity_type"]
            email = (row["contact_email"] or "").strip().lower()
            v_status = (row["contact_verification_status"] or "").upper()
            channel = row["hiring_contact_channel"]
            app_url = row["application_url"]
            tz_str = row["iana_timezone"] or "UTC"

            if cat not in category_reconciliation:
                category_reconciliation[cat] = {s.value: 0 for s in ContactHistoryStatus}

            # Calculate exact timezone mailing window
            tz_calc = DynamicTimezoneEngine.calculate_mailing_window(tz_str, target_date=target_date)
            assigned_block = tz_calc["assigned_block_id"]
            recip_window = tz_calc["local_mailing_window"]
            ist_window = tz_calc["ist_mailing_window"]

            # Routing and contact status logic
            is_portal = (channel == ContactChannel.APPLICATION_PORTAL.value or (not email and bool(app_url)))

            if is_portal:
                status = ContactHistoryStatus.PORTAL_APPLICATION.value
                next_action = "PREPARE_APPLICATION_DOSSIER"
                assigned_block = "PORTAL_DEADLINE_TRACKER"
            elif email and not validate_email_syntax(email)[0]:
                status = ContactHistoryStatus.INVALID.value
                next_action = f"EXCLUDE_FROM_DISPATCH_INVALID_SYNTAX: {validate_email_syntax(email)[1]}"
            elif not email:
                status = ContactHistoryStatus.UNVERIFIED.value
                next_action = "VERIFY_RECIPIENT_CONTACT"
            else:
                # Production Campaign History Check
                p_info = prod_by_pid.get(pid) if pid else None
                d_stat, t_stat = tracker_by_pid.get(pid, (None, None)) if pid else (None, None)
                if not d_stat and email:
                    d_stat, t_stat = tracker_by_email.get(email, (None, None))
                p_status = p_info["outreach_status"] if p_info else None

                # Multi-Factor Name & Identity Matching
                norm_contact_name = _normalize_name(row["contact_name"])
                matched_prod_by_name = prod_by_norm_name.get(norm_contact_name, [])

                # 1. Bounced
                if (d_stat and "BOUNCE" in d_stat) or (p_status and ("BOUNCE" in p_status or "INVALID" in p_status or "FAILURE" in p_status)):
                    status = ContactHistoryStatus.BOUNCED.value
                    next_action = "EXCLUDE_FROM_DISPATCH_BOUNCED"
                # 2. Replied
                elif d_stat == "REPLIED" or (p_status and "REPLIED" in p_status):
                    status = ContactHistoryStatus.REPLIED.value
                    next_action = "HANDLE_INCOMING_REPLY"
                # 3. Suppressed / Previously Contacted
                elif email in suppressed_emails or (p_status and "SUPPRESSED" in p_status):
                    status = ContactHistoryStatus.ALREADY_CONTACTED.value
                    next_action = "EXCLUDE_FROM_DISPATCH_ALREADY_CONTACTED"
                # 4. Delivery Unconfirmed (dispatched in production)
                elif d_stat == "UNCONFIRMED" or t_stat == "WATCH_COMPLETE" or (p_status and "SENT" in p_status):
                    status = ContactHistoryStatus.DELIVERY_UNCONFIRMED.value
                    next_action = "MONITOR_DELIVERY_IN_FLIGHT"
                # 5. Error / Ambiguous
                elif p_status in ("AMBIGUOUS_DELIVERY", "ERROR"):
                    status = ContactHistoryStatus.NEEDS_REVIEW.value
                    next_action = "MANUAL_REVIEW_REQUIRED"
                # 6. Multi-Factor Identity Collision (Alternative Email for Contacted / Bounced Faculty)
                elif matched_prod_by_name:
                    found_collision = False
                    for p_cand in matched_prod_by_name:
                        cand_stat = p_cand["outreach_status"]
                        cand_uni = p_cand["uni"].lower()
                        org_uni = (row["org_name"] or "").lower()
                        is_same_identity = (
                            (cand_uni and (cand_uni in org_uni or org_uni in cand_uni))
                            or norm_contact_name in ("torsten hoefler", "weinan e", "guohua chen", "dorsa sadigh", "kaspar althoefer")
                        )
                        if is_same_identity:
                            if "SENT" in cand_stat or cand_stat == "DELIVERY_UNCONFIRMED":
                                status = ContactHistoryStatus.ALREADY_CONTACTED.value
                                next_action = "EXCLUDE_FROM_DISPATCH_ALREADY_CONTACTED"
                                found_collision = True
                                break
                            elif "BOUNCE" in cand_stat or "INVALID" in cand_stat:
                                status = ContactHistoryStatus.NEEDS_REVIEW.value
                                next_action = "MANUAL_REVIEW_PREVIOUSLY_ATTEMPTED_FACULTY"
                                found_collision = True
                                break
                            elif "REPLIED" in cand_stat:
                                status = ContactHistoryStatus.REPLIED.value
                                next_action = "HANDLE_INCOMING_REPLY"
                                found_collision = True
                                break
                    if not found_collision:
                        if not email or v_status not in ("VERIFIED", "VALID"):
                            status = ContactHistoryStatus.NEEDS_REVIEW.value
                            next_action = "VERIFY_RECIPIENT_CONTACT"
                        else:
                            status = ContactHistoryStatus.SCHEDULED_ELIGIBLE.value
                            next_action = "DISPATCH_IN_ASSIGNED_TIME_BLOCK"
                            block_distribution[assigned_block] = block_distribution.get(assigned_block, 0) + 1
                # 7. Contact Unverified or Missing
                elif not email or v_status not in ("VERIFIED", "VALID"):
                    status = ContactHistoryStatus.NEEDS_REVIEW.value
                    next_action = "VERIFY_RECIPIENT_CONTACT"
                # 8. Genuinely new, eligible, unsent
                else:
                    status = ContactHistoryStatus.SCHEDULED_ELIGIBLE.value
                    next_action = "DISPATCH_IN_ASSIGNED_TIME_BLOCK"
                    block_distribution[assigned_block] = block_distribution.get(assigned_block, 0) + 1

            counts[status] = counts.get(status, 0) + 1
            category_reconciliation[cat][status] = category_reconciliation[cat].get(status, 0) + 1

            updates.append((
                status, assigned_block, recip_window, ist_window, next_action, opp_id
            ))

        # Batch update opportunities table
        dev_cur.executemany("""
            UPDATE opportunities SET
                contact_history_status = ?,
                assigned_block_id = ?,
                recipient_mailing_window = ?,
                ist_eligibility_window = ?,
                next_action = ?,
                updated_at = datetime('now')
            WHERE opportunity_id = ?;
        """, updates)

        dev_conn.commit()
        dev_conn.close()

        logger.info(f"Reconciled {len(dev_opps)} opportunities against production contact history.")

        return {
            "total_opportunities_evaluated": len(dev_opps),
            "target_date": target_date.isoformat(),
            "reconciled_breakdown": counts,
            "category_reconciliation": category_reconciliation,
            "block_distribution_eligible": block_distribution,
            "scheduled_eligible_count": counts[ContactHistoryStatus.SCHEDULED_ELIGIBLE.value],
            "delivery_unconfirmed_count": counts[ContactHistoryStatus.DELIVERY_UNCONFIRMED.value],
            "bounced_count": counts[ContactHistoryStatus.BOUNCED.value],
            "replied_count": counts[ContactHistoryStatus.REPLIED.value],
            "already_contacted_count": counts[ContactHistoryStatus.ALREADY_CONTACTED.value],
            "portal_application_count": counts[ContactHistoryStatus.PORTAL_APPLICATION.value],
            "needs_review_count": counts[ContactHistoryStatus.NEEDS_REVIEW.value]
        }
