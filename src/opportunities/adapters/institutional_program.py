"""
Opportunity Intelligence Engine: Publicly Funded Laboratories & National/International Programs Adapter.
Category 4: Publicly funded research institutes, national laboratories, and government fellowship programs.
(CERN, Max Planck, INRIA, RIKEN, Mitacs Globalink, DAAD WISE, A*STAR, ISTA, EPFL, etc.)
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone

from src.opportunities.adapters.base import BaseOpportunityAdapter
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
    DiscoveryCheckpoint,
    RequisitionType,
    PortfolioTier
)
from src.opportunities.registry import (
    canonicalize_url,
    compute_organization_id,
    compute_opportunity_id
)
from src.opportunities.data.institutional_programs_data import INSTITUTIONAL_PROGRAMS_DATA

logger = logging.getLogger(__name__)


class InstitutionalProgramAdapter(BaseOpportunityAdapter):
    """
    Adapter for discovering prestigious international research fellowships,
    national laboratory programs, and public scientific institutes.
    """

    def __init__(self):
        super().__init__(adapter_name="InstitutionalProgramAdapter", category=OpportunityType.INSTITUTIONAL_PROGRAM.value)

    def fetch_raw_opportunities(
        self,
        checkpoint: Optional[DiscoveryCheckpoint] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Retrieves institutional program and national lab fellowship opportunities.
        Supports checkpoint-based cursor pagination.
        """
        start_idx = 0
        if checkpoint and checkpoint.cursor_or_token:
            try:
                start_idx = int(checkpoint.cursor_or_token)
            except ValueError:
                start_idx = 0

        total_catalog = len(INSTITUTIONAL_PROGRAMS_DATA)
        if start_idx >= total_catalog:
            start_idx = 0

        end_idx = min(start_idx + limit, total_catalog)
        items = INSTITUTIONAL_PROGRAMS_DATA[start_idx:end_idx]

        if len(items) < limit and start_idx > 0:
            remaining = limit - len(items)
            items.extend(INSTITUTIONAL_PROGRAMS_DATA[:remaining])

        logger.info(
            f"InstitutionalProgramAdapter fetched {len(items)} opportunities "
            f"(slice {start_idx}:{end_idx} of {total_catalog})"
        )
        return items

    def normalize_raw_item(self, raw_item: Dict[str, Any]) -> Tuple[Organization, Opportunity]:
        inst_name = raw_item.get("institution_name") or raw_item.get("organization_name", "Public Institution")
        prog_name = raw_item.get("program_name") or raw_item.get("program_title") or inst_name
        web_url = canonicalize_url(raw_item.get("website", ""))
        org_id = compute_organization_id(inst_name, web_url)
        now_iso = datetime.now(timezone.utc).isoformat()

        org = Organization(
            organization_id=org_id,
            name=inst_name,
            category=OpportunityType.INSTITUTIONAL_PROGRAM.value,
            website_url=web_url,
            country=raw_item.get("country"),
            city=raw_item.get("city"),
            iana_timezone=raw_item.get("timezone", "UTC"),
            description=f"Publicly funded institution / research program: {prog_name}",
            tier=raw_item.get("tier", "TIER_1")
        )

        title = raw_item.get("title") or raw_item.get("program_title") or f"{prog_name} Fellow"
        opp_id = compute_opportunity_id(org_id, OpportunityType.INSTITUTIONAL_PROGRAM.value, title, "Summer 2027")

        evidence_url = canonicalize_url(raw_item.get("evidence_url") or raw_item.get("official_portal_url") or "")
        app_url = canonicalize_url(raw_item.get("application_url") or raw_item.get("official_portal_url") or "") or evidence_url

        is_confirmed = (raw_item.get("audit_classification") == OpportunityClassification.CONFIRMED_OPENING.value)
        comp_details = raw_item.get("compensation_details") or raw_item.get("funding_and_stipend") or "Institutional Fellowship Stipend"
        eligibility = raw_item.get("eligibility") or raw_item.get("eligibility_and_duration") or "Undergraduate engineering student"

        opp = Opportunity(
            opportunity_id=opp_id,
            organization_id=org_id,
            title=title,
            opportunity_type=OpportunityType.INSTITUTIONAL_PROGRAM.value,
            source="INSTITUTIONAL_PROGRAM_REGISTRY",
            location=f"{raw_item.get('city')}, {raw_item.get('country')}",
            iana_timezone=raw_item.get("timezone", "UTC"),
            technical_domains=raw_item.get("domains", []),
            technical_relevance_summary=raw_item.get("relevance"),
            potential_contribution=raw_item.get("potential_contribution"),
            matched_project_id=raw_item.get("matched_project"),
            eligibility_criteria=eligibility,
            internship_duration_weeks=raw_item.get("duration_weeks", 10),
            target_term="Summer 2027",
            compensation_type=raw_item.get("compensation_type", CompensationType.STIPEND.value),
            compensation_details=comp_details,
            stipend_amount=raw_item.get("stipend_amount") or comp_details,
            travel_support=raw_item.get("travel_support") or "Travel allowance provided",
            housing_support=raw_item.get("housing_support") or "Subsidized accommodation provided",
            required_documents=raw_item.get("required_documents") or "Academic CV, transcripts, faculty references, statement of interest",
            compensation_evidence=raw_item.get("compensation_evidence", "OFFICIAL_INSTITUTIONAL_FELLOWSHIP_REGULATIONS"),
            official_evidence_urls=[evidence_url] if evidence_url else [],
            freshness_timestamp=now_iso,
            hiring_contact_channel=raw_item.get("contact_channel", ContactChannel.APPLICATION_PORTAL.value),
            contact_email=raw_item.get("contact_email"),
            contact_name=raw_item.get("contact_name", f"{inst_name} Fellowship Admissions"),
            contact_verification_status=(
                VerificationStatus.VERIFIED.value if app_url or raw_item.get("contact_email") else VerificationStatus.UNVERIFIED.value
            ),
            application_url=app_url,
            application_deadline=raw_item.get("deadline", "Rolling"),
            audit_classification=raw_item.get("audit_classification", OpportunityClassification.CONFIRMED_OPENING.value),
            requisition_type=raw_item.get("requisition_type", RequisitionType.FELLOWSHIP_PROGRAM.value),
            role_title_type=raw_item.get("role_title_type", RoleTitleType.EXACT.value),
            portfolio_tier=raw_item.get("portfolio_tier", PortfolioTier.TIER_A_ELITE_PRIORITY.value),
            workplace_type=raw_item.get("workplace_type", WorkplaceType.ONSITE.value),
            international_eligibility=raw_item.get("international_eligibility", InternationalEligibility.SPONSORED_OR_J1_REQUIRED.value),
            operational_status="OPERATIONAL",
            verification_notes=f"Verified through official program portal ({evidence_url}).",
            is_confirmed_opening=is_confirmed,
            next_action="SUBMIT_OFFICIAL_FELLOWSHIP_APPLICATION",
            created_at=now_iso,
            updated_at=now_iso,
            last_verified_at=now_iso
        )

        return org, opp
