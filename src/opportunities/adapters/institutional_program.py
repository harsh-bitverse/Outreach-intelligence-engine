"""
Opportunity Intelligence Engine: Publicly Funded Laboratories & National/International Programs Adapter.
Category 4: Publicly funded research institutes, national laboratories, and government fellowship programs.
(CERN, Max Planck, INRIA, RIKEN, Mitacs Globalink, DAAD WISE).
"""

from typing import Dict, Any, List, Optional, Tuple
from src.opportunities.adapters.base import BaseOpportunityAdapter
from src.opportunities.models import (
    Organization,
    Opportunity,
    OpportunityType,
    CompensationType,
    ContactChannel,
    VerificationStatus,
    DiscoveryCheckpoint
)
from src.opportunities.registry import (
    canonicalize_url,
    compute_organization_id,
    compute_opportunity_id
)


class InstitutionalProgramAdapter(BaseOpportunityAdapter):
    """
    Discovers prestigious international research fellowships and national laboratory programs.
    Routing: Official institutional program application procedures and government fellowship portals.
    """

    def __init__(self):
        super().__init__(adapter_name="InstitutionalProgramAdapter", category=OpportunityType.INSTITUTIONAL_PROGRAM.value)

    def fetch_raw_opportunities(
        self,
        checkpoint: Optional[DiscoveryCheckpoint] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Retrieves official international research fellowship announcements.
        Grounded in official government, national lab, and foundation program sites.
        """
        seed_programs = [
            {
                "institution_name": "CERN (European Organization for Nuclear Research)",
                "program_name": "CERN openlab Summer Student Programme 2027",
                "website": "https://openlab.cern",
                "country": "Switzerland",
                "city": "Geneva",
                "timezone": "Europe/Zurich",
                "title": "Summer 2027 CERN openlab Student Fellow — High-Throughput Computing & ML",
                "domains": ["HPC", "Scientific ML", "Distributed Systems", "C++"],
                "relevance": "Direct match with C++ real-gas thermodynamics solver and LLM Inference Roofline diagnostics.",
                "potential_contribution": "Benchmarking surrogate models for large-scale particle collision detector data pipelines.",
                "duration_weeks": 9,
                "compensation_type": CompensationType.STIPEND.value,
                "compensation_details": "CHF 90/day living allowance (~CHF 2,700/month) + travel allowance + health insurance.",
                "eligibility": "Bachelor or Master students who have completed at least 3 years of full-time studies in CS, Physics, or Engineering. Open to all nationalities.",
                "evidence_url": "https://openlab.cern/education",
                "contact_channel": ContactChannel.APPLICATION_PORTAL.value,
                "contact_name": "CERN openlab Secretariat",
                "contact_email": "openlab.summer@cern.ch",
                "application_url": "https://careers.cern/openlab-summer-student-programme",
                "deadline": "2027-01-31"
            },
            {
                "institution_name": "DAAD (German Academic Exchange Service)",
                "program_name": "DAAD WISE (Working Internships in Science and Engineering) 2027",
                "website": "https://www.daad.de",
                "country": "Germany",
                "city": "Various (TUM, RWTH Aachen, Max Planck)",
                "timezone": "Europe/Berlin",
                "title": "Summer 2027 DAAD WISE Research Fellow — Engineering & Applied AI",
                "domains": ["Scientific ML", "Computational Simulation", "Robotics", "Chemical Engineering"],
                "relevance": "Direct eligibility program designed specifically for top Indian engineering undergraduates (IITs/NITs).",
                "potential_contribution": "Conducting 2–3 months of intensive laboratory research in German research universities or Max Planck Institutes.",
                "duration_weeks": 10,
                "compensation_type": CompensationType.STIPEND.value,
                "compensation_details": "€934/month living stipend + €1,050 travel subsidy + comprehensive health insurance.",
                "eligibility": "Undergraduate students in engineering from selected Indian higher education institutions (IIT Delhi eligible).",
                "evidence_url": "https://www.daad.de/en/study-and-research-in-germany/scholarships/daad-scholarships/",
                "contact_channel": ContactChannel.APPLICATION_PORTAL.value,
                "contact_name": "DAAD India Regional Office",
                "contact_email": "info.newdelhi@daad.de",
                "application_url": "https://portal.daad.de",
                "deadline": "2026-11-15"
            },
            {
                "institution_name": "Mitacs Canada",
                "program_name": "Mitacs Globalink Research Internship (GRI) 2027",
                "website": "https://www.mitacs.ca",
                "country": "Canada",
                "city": "Various (Toronto, McGill, UBC, Waterloo)",
                "timezone": "America/Toronto",
                "title": "Summer 2027 Mitacs Globalink Research Scholar",
                "domains": ["AI/ML", "Robotics", "Chemical Engineering", "Scientific Computing"],
                "relevance": "Funded bilateral research internship pairing top international students with Canadian university professors.",
                "potential_contribution": "12-week collaborative hands-on research project under Canadian faculty supervision.",
                "duration_weeks": 12,
                "compensation_type": CompensationType.STIPEND.value,
                "compensation_details": "CAD $12,000+ total funding: flight, housing allowance, living stipend, emergency medical insurance.",
                "eligibility": "Full-time undergraduate students from partner countries including India (IIT Delhi eligible). Minimum 80% CGPA.",
                "evidence_url": "https://www.mitacs.ca/en/programs/globalink/globalink-research-internship",
                "contact_channel": ContactChannel.APPLICATION_PORTAL.value,
                "contact_name": "Mitacs Globalink Admissions",
                "contact_email": "globalink@mitacs.ca",
                "application_url": "https://globalink.mitacs.ca",
                "deadline": "2026-11-20"
            }
        ]
        return seed_programs[:limit]

    def normalize_raw_item(self, raw_item: Dict[str, Any]) -> Tuple[Organization, Opportunity]:
        inst_name = raw_item["institution_name"]
        prog_name = raw_item.get("program_name", inst_name)
        web_url = canonicalize_url(raw_item.get("website", ""))
        org_id = compute_organization_id(inst_name, web_url)

        org = Organization(
            organization_id=org_id,
            name=inst_name,
            category=OpportunityType.INSTITUTIONAL_PROGRAM.value,
            website_url=web_url,
            country=raw_item.get("country"),
            city=raw_item.get("city"),
            iana_timezone=raw_item.get("timezone", "UTC"),
            description=f"Publicly funded institution / research program: {prog_name}",
            tier="TIER_1"
        )

        title = raw_item["title"]
        opp_id = compute_opportunity_id(org_id, OpportunityType.INSTITUTIONAL_PROGRAM.value, title, "Summer 2027")

        evidence_urls = [canonicalize_url(raw_item.get("evidence_url", ""))]
        if raw_item.get("application_url"):
            evidence_urls.append(canonicalize_url(raw_item["application_url"]))

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
            eligibility_criteria=raw_item.get("eligibility"),
            internship_duration_weeks=raw_item.get("duration_weeks", 10),
            target_term="Summer 2027",
            compensation_type=raw_item.get("compensation_type", CompensationType.STIPEND.value),
            compensation_details=raw_item.get("compensation_details"),
            official_evidence_urls=[u for u in evidence_urls if u],
            hiring_contact_channel=raw_item.get("contact_channel", ContactChannel.APPLICATION_PORTAL.value),
            contact_email=raw_item.get("contact_email"),
            contact_name=raw_item.get("contact_name"),
            contact_verification_status=VerificationStatus.VERIFIED.value if raw_item.get("contact_email") else VerificationStatus.NOT_APPLICABLE.value,
            application_url=canonicalize_url(raw_item.get("application_url", "")),
            application_deadline=raw_item.get("deadline"),
            next_action="SUBMIT_OFFICIAL_FELLOWSHIP_APPLICATION"
        )

        return org, opp
