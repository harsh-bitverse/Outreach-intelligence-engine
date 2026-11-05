"""
Opportunity Intelligence Engine: Repository Layer.
Handles durable SQLite persistence for organizations, opportunities, runs, and checkpoints.
"""

import json
import sqlite3
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from pathlib import Path

from src.opportunities.models import (
    Organization,
    Opportunity,
    QualificationBreakdown,
    DiscoveryCheckpoint,
    DiscoveryRun
)
from src.mail.bounce_reconciler import clean_email_address


class OpportunityRepository:
    """
    Data access layer for normalized opportunities, organizations, and discovery state.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row

    # --------------------------------------------------------------------------
    # Organizations
    # --------------------------------------------------------------------------
    def save_organization(self, org: Organization) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = self.conn.cursor()
        cur.execute("""
            INSERT INTO organizations (
                organization_id, name, category, website_url, country, city,
                iana_timezone, description, funding_stage, tier, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(organization_id) DO UPDATE SET
                name = excluded.name,
                category = excluded.category,
                website_url = excluded.website_url,
                country = excluded.country,
                city = excluded.city,
                iana_timezone = excluded.iana_timezone,
                description = excluded.description,
                funding_stage = excluded.funding_stage,
                tier = excluded.tier,
                updated_at = excluded.updated_at;
        """, (
            org.organization_id, org.name, org.category, org.website_url,
            org.country, org.city, org.iana_timezone, org.description,
            org.funding_stage, org.tier, org.created_at or now_iso, now_iso
        ))
        self.conn.commit()

    def _row_to_organization(self, row: sqlite3.Row) -> Organization:
        return Organization(
            organization_id=row["organization_id"],
            name=row["name"],
            category=row["category"],
            website_url=row["website_url"],
            country=row["country"],
            city=row["city"],
            iana_timezone=row["iana_timezone"] or "UTC",
            description=row["description"],
            funding_stage=row["funding_stage"],
            tier=row["tier"] or "STANDARD",
            created_at=row["created_at"],
            updated_at=row["updated_at"]
        )

    def get_organization(self, org_id: str) -> Optional[Organization]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM organizations WHERE organization_id = ?;", (org_id,))
        row = cur.fetchone()
        if not row:
            return None
        return self._row_to_organization(row)

    # --------------------------------------------------------------------------
    # Opportunities
    # --------------------------------------------------------------------------
    def save_opportunity(self, opp: Opportunity) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        tech_domains_json = json.dumps(opp.technical_domains or [])
        evidence_urls_json = json.dumps(opp.official_evidence_urls or [])
        score_breakdown_json = json.dumps(opp.score_breakdown.to_dict()) if opp.score_breakdown else None

        cur = self.conn.cursor()
        cur.execute("""
            INSERT INTO opportunities (
                opportunity_id, organization_id, title, opportunity_type, source,
                location, iana_timezone, technical_domains, technical_relevance_summary,
                potential_contribution, matched_project_id, eligibility_criteria,
                internship_duration_weeks, target_term, compensation_type,
                compensation_details, official_evidence_urls, freshness_timestamp,
                hiring_contact_channel, contact_email, contact_name,
                contact_verification_status, application_url, application_deadline,
                qualification_score, score_breakdown, priority_tier,
                portfolio_tier, requisition_type,
                audit_classification, role_title_type, workplace_type,
                international_eligibility, compensation_evidence,
                stipend_amount, travel_support, housing_support, required_documents,
                operational_status, verification_notes, last_verified_at,
                discovery_status, application_status, is_confirmed_opening, next_action,
                created_at, updated_at, last_checked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(opportunity_id) DO UPDATE SET
                title = excluded.title,
                location = excluded.location,
                iana_timezone = excluded.iana_timezone,
                technical_domains = excluded.technical_domains,
                technical_relevance_summary = excluded.technical_relevance_summary,
                potential_contribution = excluded.potential_contribution,
                matched_project_id = excluded.matched_project_id,
                eligibility_criteria = excluded.eligibility_criteria,
                internship_duration_weeks = excluded.internship_duration_weeks,
                compensation_type = excluded.compensation_type,
                compensation_details = excluded.compensation_details,
                official_evidence_urls = excluded.official_evidence_urls,
                freshness_timestamp = excluded.freshness_timestamp,
                hiring_contact_channel = excluded.hiring_contact_channel,
                contact_email = excluded.contact_email,
                contact_name = excluded.contact_name,
                contact_verification_status = excluded.contact_verification_status,
                application_url = excluded.application_url,
                application_deadline = excluded.application_deadline,
                qualification_score = excluded.qualification_score,
                score_breakdown = excluded.score_breakdown,
                priority_tier = excluded.priority_tier,
                portfolio_tier = excluded.portfolio_tier,
                requisition_type = excluded.requisition_type,
                audit_classification = excluded.audit_classification,
                role_title_type = excluded.role_title_type,
                workplace_type = excluded.workplace_type,
                international_eligibility = excluded.international_eligibility,
                compensation_evidence = excluded.compensation_evidence,
                stipend_amount = excluded.stipend_amount,
                travel_support = excluded.travel_support,
                housing_support = excluded.housing_support,
                required_documents = excluded.required_documents,
                operational_status = excluded.operational_status,
                verification_notes = excluded.verification_notes,
                last_verified_at = excluded.last_verified_at,
                discovery_status = excluded.discovery_status,
                application_status = excluded.application_status,
                is_confirmed_opening = excluded.is_confirmed_opening,
                next_action = excluded.next_action,
                updated_at = excluded.updated_at,
                last_checked_at = excluded.last_checked_at;
        """, (
            opp.opportunity_id, opp.organization_id, opp.title, opp.opportunity_type, opp.source,
            opp.location, opp.iana_timezone, tech_domains_json, opp.technical_relevance_summary,
            opp.potential_contribution, opp.matched_project_id, opp.eligibility_criteria,
            opp.internship_duration_weeks, opp.target_term, opp.compensation_type,
            opp.compensation_details, evidence_urls_json, opp.freshness_timestamp,
            opp.hiring_contact_channel, clean_email_address(opp.contact_email), opp.contact_name,
            opp.contact_verification_status, opp.application_url, opp.application_deadline,
            opp.qualification_score, score_breakdown_json, opp.priority_tier,
            opp.portfolio_tier, opp.requisition_type,
            opp.audit_classification, opp.role_title_type, opp.workplace_type,
            opp.international_eligibility, opp.compensation_evidence,
            opp.stipend_amount, opp.travel_support, opp.housing_support, opp.required_documents,
            opp.operational_status, opp.verification_notes, opp.last_verified_at,
            opp.discovery_status, opp.application_status, 1 if opp.is_confirmed_opening else 0,
            opp.next_action, opp.created_at or now_iso, now_iso, now_iso
        ))
        self.conn.commit()

    def get_opportunity(self, opp_id: str) -> Optional[Opportunity]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM opportunities WHERE opportunity_id = ?;", (opp_id,))
        row = cur.fetchone()
        if not row:
            return None
        return self._row_to_opportunity(row)

    def list_opportunities(
        self,
        category: Optional[str] = None,
        discovery_status: Optional[str] = None,
        min_score: Optional[float] = None,
        limit: int = 100
    ) -> List[Opportunity]:
        cur = self.conn.cursor()
        conditions = []
        params = []

        if category:
            conditions.append("opportunity_type = ?")
            params.append(category)
        if discovery_status:
            conditions.append("discovery_status = ?")
            params.append(discovery_status)
        if min_score is not None:
            conditions.append("qualification_score >= ?")
            params.append(min_score)

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        query = f"SELECT * FROM opportunities {where_clause} ORDER BY qualification_score DESC LIMIT ?;"
        params.append(limit)

        cur.execute(query, params)
        return [self._row_to_opportunity(r) for r in cur.fetchall()]

    def is_opportunity_duplicate(
        self,
        opp_id: str,
        canonical_application_url: Optional[str] = None,
        contact_email: Optional[str] = None
    ) -> bool:
        cur = self.conn.cursor()
        # 1. By ID
        cur.execute("SELECT 1 FROM opportunities WHERE opportunity_id = ?;", (opp_id,))
        if cur.fetchone():
            return True

        # 2. By Application URL if provided
        if canonical_application_url and len(canonical_application_url) > 10:
            cur.execute("SELECT 1 FROM opportunities WHERE application_url = ?;", (canonical_application_url,))
            if cur.fetchone():
                return True

        return False

    def is_contact_suppressed_or_already_contacted(self, email: Optional[str]) -> bool:
        """
        Cross-checks the candidate email against suppression_list, professors, and delivery tracker.
        Ensures strict deduplication and compliance across legacy outreach and new opportunities.
        """
        if not email:
            return False
        clean = clean_email_address(email)
        cur = self.conn.cursor()

        # Check suppression_list if table exists
        try:
            cur.execute("SELECT 1 FROM suppression_list WHERE lower(email) = ?;", (clean.lower(),))
            if cur.fetchone():
                return True
        except Exception:
            pass

        # Check legacy professors table
        try:
            cur.execute("SELECT 1 FROM professors WHERE lower(email) = ? AND outreach_status NOT IN ('AWAITING_DRAFT');", (clean.lower(),))
            if cur.fetchone():
                return True
        except Exception:
            pass

        # Check outbound_delivery_tracker
        try:
            cur.execute("SELECT 1 FROM outbound_delivery_tracker WHERE lower(recipient_email) = ?;", (clean.lower(),))
            if cur.fetchone():
                return True
        except Exception:
            pass

        return False

    # --------------------------------------------------------------------------
    # Checkpoints & Runs
    # --------------------------------------------------------------------------
    def get_checkpoint(self, source_key: str) -> Optional[DiscoveryCheckpoint]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM discovery_checkpoints WHERE source_key = ?;", (source_key,))
        row = cur.fetchone()
        if not row:
            return None
        return DiscoveryCheckpoint(
            source_key=row["source_key"],
            category=row["category"],
            last_queried_at=row["last_queried_at"],
            cursor_or_token=row["cursor_or_token"],
            items_discovered_count=row["items_discovered_count"],
            status=row["status"],
            error_message=row["error_message"],
            updated_at=row["updated_at"]
        )

    def save_checkpoint(self, checkpoint: DiscoveryCheckpoint) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = self.conn.cursor()
        cur.execute("""
            INSERT INTO discovery_checkpoints (
                source_key, category, last_queried_at, cursor_or_token,
                items_discovered_count, status, error_message, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_key) DO UPDATE SET
                category = excluded.category,
                last_queried_at = excluded.last_queried_at,
                cursor_or_token = excluded.cursor_or_token,
                items_discovered_count = excluded.items_discovered_count,
                status = excluded.status,
                error_message = excluded.error_message,
                updated_at = excluded.updated_at;
        """, (
            checkpoint.source_key, checkpoint.category, checkpoint.last_queried_at,
            checkpoint.cursor_or_token, checkpoint.items_discovered_count,
            checkpoint.status, checkpoint.error_message, now_iso
        ))
        self.conn.commit()

    def save_discovery_run(self, run: DiscoveryRun) -> None:
        errors_json = json.dumps(run.errors or [])
        cur = self.conn.cursor()
        cur.execute("""
            INSERT INTO discovery_runs (
                run_id, adapter_name, started_at, completed_at,
                opportunities_found, opportunities_qualified, errors, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                completed_at = excluded.completed_at,
                opportunities_found = excluded.opportunities_found,
                opportunities_qualified = excluded.opportunities_qualified,
                errors = excluded.errors,
                status = excluded.status;
        """, (
            run.run_id, run.adapter_name, run.started_at, run.completed_at,
            run.opportunities_found, run.opportunities_qualified, errors_json, run.status
        ))
        self.conn.commit()

    # --------------------------------------------------------------------------
    # Helper
    # --------------------------------------------------------------------------
    def _row_to_opportunity(self, row: sqlite3.Row) -> Opportunity:
        tech_domains = json.loads(row["technical_domains"]) if row["technical_domains"] else []
        evidence_urls = json.loads(row["official_evidence_urls"]) if row["official_evidence_urls"] else []
        breakdown = None
        if row["score_breakdown"]:
            try:
                b_dict = json.loads(row["score_breakdown"])
                breakdown = QualificationBreakdown(**b_dict)
            except Exception:
                pass

        priority_tier = row["priority_tier"] if "priority_tier" in row.keys() else "NEEDS_VERIFICATION"
        is_confirmed = bool(row["is_confirmed_opening"]) if "is_confirmed_opening" in row.keys() else True
        audit_classification = row["audit_classification"] if "audit_classification" in row.keys() else "NEEDS_VERIFICATION"
        role_title_type = row["role_title_type"] if "role_title_type" in row.keys() else "PROPOSED_OUTREACH_FOCUS"
        workplace_type = row["workplace_type"] if "workplace_type" in row.keys() else "UNKNOWN"
        international_eligibility = row["international_eligibility"] if "international_eligibility" in row.keys() else "NEEDS_CONFIRMATION"
        compensation_evidence = row["compensation_evidence"] if "compensation_evidence" in row.keys() else "UNPUBLISHED_BY_EMPLOYER"
        operational_status = row["operational_status"] if "operational_status" in row.keys() else "OPERATIONAL"
        verification_notes = row["verification_notes"] if "verification_notes" in row.keys() else None
        last_verified_at = row["last_verified_at"] if "last_verified_at" in row.keys() else None

        portfolio_tier = row["portfolio_tier"] if "portfolio_tier" in row.keys() else "TIER_B_STRONG_MATCH"
        requisition_type = row["requisition_type"] if "requisition_type" in row.keys() else "PROSPECTIVE_PITCH_TARGET"
        stipend_amount = row["stipend_amount"] if "stipend_amount" in row.keys() else None
        travel_support = row["travel_support"] if "travel_support" in row.keys() else None
        housing_support = row["housing_support"] if "housing_support" in row.keys() else None
        required_documents = row["required_documents"] if "required_documents" in row.keys() else None

        return Opportunity(
            opportunity_id=row["opportunity_id"],
            organization_id=row["organization_id"],
            title=row["title"],
            opportunity_type=row["opportunity_type"],
            source=row["source"],
            location=row["location"],
            iana_timezone=row["iana_timezone"] or "UTC",
            technical_domains=tech_domains,
            technical_relevance_summary=row["technical_relevance_summary"],
            potential_contribution=row["potential_contribution"],
            matched_project_id=row["matched_project_id"],
            eligibility_criteria=row["eligibility_criteria"],
            internship_duration_weeks=row["internship_duration_weeks"] or 10,
            target_term=row["target_term"] or "Summer 2027",
            compensation_type=row["compensation_type"] or "UNKNOWN",
            compensation_details=row["compensation_details"],
            official_evidence_urls=evidence_urls,
            freshness_timestamp=row["freshness_timestamp"],
            hiring_contact_channel=row["hiring_contact_channel"] or "UNSPECIFIED",
            contact_email=row["contact_email"],
            contact_name=row["contact_name"],
            contact_verification_status=row["contact_verification_status"] or "UNVERIFIED",
            application_url=row["application_url"],
            application_deadline=row["application_deadline"],
            qualification_score=row["qualification_score"] or 0.0,
            score_breakdown=breakdown,
            priority_tier=priority_tier,
            portfolio_tier=portfolio_tier,
            audit_classification=audit_classification,
            requisition_type=requisition_type,
            role_title_type=role_title_type,
            workplace_type=workplace_type,
            international_eligibility=international_eligibility,
            compensation_evidence=compensation_evidence,
            stipend_amount=stipend_amount,
            travel_support=travel_support,
            housing_support=housing_support,
            required_documents=required_documents,
            operational_status=operational_status,
            verification_notes=verification_notes,
            last_verified_at=last_verified_at,
            discovery_status=row["discovery_status"] or "DISCOVERED",
            application_status=row["application_status"] or "NOT_APPLIED",
            is_confirmed_opening=is_confirmed,
            next_action=row["next_action"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_checked_at=row["last_checked_at"]
        )
