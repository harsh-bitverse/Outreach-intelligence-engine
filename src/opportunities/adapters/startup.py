"""
Opportunity Intelligence Engine: Technical Startups Adapter.
Category 2: Indian and International funded, technically ambitious startups.
Focuses on AI/ML infrastructure, inference systems, robotics, autonomy, energy, and aerospace.
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
from src.opportunities.adapters.base import BaseOpportunityAdapter
from src.opportunities.models import (
    Organization,
    Opportunity,
    OpportunityType,
    CompensationType,
    ContactChannel,
    VerificationStatus,
    DiscoveryCheckpoint,
    PriorityTier
)
from src.opportunities.registry import (
    canonicalize_url,
    compute_organization_id,
    compute_opportunity_id
)
from src.opportunities.data.startup_catalog import STARTUP_CATALOG

logger = logging.getLogger(__name__)


class StartupAdapter(BaseOpportunityAdapter):
    """
    Discovers high-growth, technically ambitious startups offering hands-on engineering ownership.
    Routing:
    - If contact_channel is EMAIL: High-agency personal pitch to Founder / Head of AI & Eng.
    - If contact_channel is APPLICATION_PORTAL: Tailored submission through official careers portal.
    """

    def __init__(self):
        super().__init__(adapter_name="StartupAdapter", category=OpportunityType.STARTUP.value)

    def fetch_raw_opportunities(
        self,
        checkpoint: Optional[DiscoveryCheckpoint] = None,
        limit: int = 150
    ) -> List[Dict[str, Any]]:
        """
        Retrieves real frontier startup engineering opportunities.
        Supports resume/pagination using checkpoint cursor.
        """
        start_idx = 0
        if checkpoint and checkpoint.cursor_or_token:
            try:
                start_idx = int(checkpoint.cursor_or_token)
            except ValueError:
                start_idx = 0

        total_catalog_size = len(STARTUP_CATALOG)
        if start_idx >= total_catalog_size:
            start_idx = 0  # Wrap around for recurring sweeps

        end_idx = min(start_idx + limit, total_catalog_size)
        items = STARTUP_CATALOG[start_idx:end_idx]

        # If requesting more than remainder, wrap around to fill up to limit
        if len(items) < limit and start_idx > 0:
            remaining = limit - len(items)
            items.extend(STARTUP_CATALOG[:remaining])

        logger.info(
            f"StartupAdapter fetched {len(items)} opportunities "
            f"(slice {start_idx}:{end_idx} of {total_catalog_size})"
        )
        return items

    def normalize_raw_item(self, raw_item: Dict[str, Any]) -> Tuple[Organization, Opportunity]:
        company_name = raw_item["company_name"]
        web_url = canonicalize_url(raw_item.get("website", ""))
        org_id = compute_organization_id(company_name, web_url)

        funding_stage = raw_item.get("funding_stage", "SERIES_A")
        tier = "TIER_1" if funding_stage in ("SERIES_A", "SERIES_B", "SERIES_C", "GROWTH") else "STANDARD"

        desc_parts = [
            f"Technically ambitious startup in {', '.join(raw_item.get('domains', ['Tech'])[:2])}.",
            f"Funding: {raw_item.get('disclosed_funding', 'Venture backed')}."
        ]
        if raw_item.get("founders"):
            desc_parts.append(f"Leadership: {raw_item['founders']}.")

        org = Organization(
            organization_id=org_id,
            name=company_name,
            category=OpportunityType.STARTUP.value,
            website_url=web_url,
            country=raw_item.get("country"),
            city=raw_item.get("city"),
            iana_timezone=raw_item.get("timezone", "UTC"),
            description=" ".join(desc_parts),
            funding_stage=funding_stage,
            tier=tier
        )

        title = raw_item["title"]
        opp_id = compute_opportunity_id(org_id, OpportunityType.STARTUP.value, title, "Summer 2027")

        evidence_urls = [canonicalize_url(raw_item.get("evidence_url", ""))]
        if raw_item.get("application_url"):
            app_clean = canonicalize_url(raw_item["application_url"])
            if app_clean not in evidence_urls:
                evidence_urls.append(app_clean)

        channel = raw_item.get("contact_channel", ContactChannel.EMAIL.value)
        next_act = (
            "TAILOR_STARTUP_ENGINEERING_PITCH"
            if channel == ContactChannel.EMAIL.value
            else "PREPARE_STARTUP_PORTAL_APPLICATION"
        )

        opp = Opportunity(
            opportunity_id=opp_id,
            organization_id=org_id,
            title=title,
            opportunity_type=OpportunityType.STARTUP.value,
            source="STARTUP_FRONTIER_REGISTRY",
            location=f"{raw_item.get('city')}, {raw_item.get('country')}",
            iana_timezone=raw_item.get("timezone", "UTC"),
            technical_domains=raw_item.get("domains", []),
            technical_relevance_summary=raw_item.get("relevance"),
            potential_contribution=raw_item.get("potential_contribution"),
            eligibility_criteria=raw_item.get("eligibility", "Undergraduate engineering students"),
            internship_duration_weeks=raw_item.get("duration_weeks", 12),
            target_term="Summer 2027",
            compensation_type=raw_item.get("compensation_type", CompensationType.PAID.value),
            compensation_details=raw_item.get("compensation_details"),
            official_evidence_urls=[u for u in evidence_urls if u],
            hiring_contact_channel=channel,
            contact_email=raw_item.get("contact_email"),
            contact_name=raw_item.get("contact_name"),
            contact_verification_status=(
                VerificationStatus.VERIFIED.value
                if raw_item.get("contact_email")
                else VerificationStatus.UNVERIFIED.value
            ),
            application_url=canonicalize_url(raw_item.get("application_url", "")),
            application_deadline=raw_item.get("deadline", "Rolling"),
            is_confirmed_opening=raw_item.get("is_confirmed_opening", True),
            next_action=next_act
        )

        return org, opp
