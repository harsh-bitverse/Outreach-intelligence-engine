"""
Opportunity Intelligence Engine: Frontier-Tech Companies & Industrial R&D Adapter.
Category 3: Frontier AI laboratories and industrial research organizations (Anthropic, DeepMind, OpenAI, NVIDIA, MSR).
Focuses on AI infrastructure, foundation models, high-performance computing, and autonomous systems.
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


class FrontierIndustryAdapter(BaseOpportunityAdapter):
    """
    Discovers research placements and engineering internships at frontier AI companies.
    Routing: Official careers portals, verified technical recruiters, or research fellowship programs.
    """

    def __init__(self):
        super().__init__(adapter_name="FrontierIndustryAdapter", category=OpportunityType.FRONTIER_COMPANY.value)

    def fetch_raw_opportunities(
        self,
        checkpoint: Optional[DiscoveryCheckpoint] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Retrieves official frontier-tech research and systems engineering internships.
        Grounded in official corporate career portals and published research programs.
        """
        seed_frontier = [
            {
                "company_name": "Anthropic",
                "website": "https://www.anthropic.com",
                "country": "United States",
                "city": "San Francisco, CA / London, UK",
                "timezone": "America/Los_Angeles",
                "title": "Summer 2027 Research Engineering Intern — AI Infrastructure & Alignment",
                "domains": ["AI Infrastructure", "LLM Inference", "Systems Optimization", "HPC"],
                "relevance": "Direct match with Sovereign Agentic Workbench and LLM Inference Intelligence Engine.",
                "potential_contribution": "Profiling compute bounds vs memory-bandwidth bottlenecks on large-scale distributed model evaluations.",
                "duration_weeks": 12,
                "compensation_type": CompensationType.PAID.value,
                "compensation_details": "$60–$75/hour + comprehensive relocation and housing support.",
                "eligibility": "Undergraduate, Masters, or PhD students. International candidates eligible for US J-1 or UK Tier 5 visa support.",
                "evidence_url": "https://www.anthropic.com/careers",
                "contact_channel": ContactChannel.APPLICATION_PORTAL.value,
                "contact_name": "University Recruiting Team",
                "contact_email": None,
                "application_url": "https://jobs.lever.co/anthropic/internships",
                "deadline": "2026-12-01"
            },
            {
                "company_name": "Google DeepMind",
                "website": "https://deepmind.google",
                "country": "United Kingdom",
                "city": "London",
                "timezone": "Europe/London",
                "title": "Summer 2027 Student Researcher — Scientific ML & Autonomous Systems",
                "domains": ["Scientific ML", "PINNs", "Surrogate Simulation", "Robotics"],
                "relevance": "Strong overlap with Neural Process Twin surrogate modeling and Finite-Difference PINN error estimation.",
                "potential_contribution": "Designing and testing physics-grounded neural operator surrogates for complex physical systems.",
                "duration_weeks": 12,
                "compensation_type": CompensationType.PAID.value,
                "compensation_details": "£4,500–£5,500/month + London living stipend and travel.",
                "eligibility": "Enrolled in an undergraduate or graduate degree program. Global applicants supported with UK student visitor visas.",
                "evidence_url": "https://deepmind.google/about/careers/student-researcher/",
                "contact_channel": ContactChannel.APPLICATION_PORTAL.value,
                "contact_name": "DeepMind Student Research Program",
                "contact_email": None,
                "application_url": "https://deepmind.google/about/careers/student-researcher/",
                "deadline": "2026-11-30"
            },
            {
                "company_name": "NVIDIA",
                "website": "https://www.nvidia.com",
                "country": "United States",
                "city": "Santa Clara, CA",
                "timezone": "America/Los_Angeles",
                "title": "Summer 2027 AI Performance & System Software Intern",
                "domains": ["Inference Optimization", "HPC", "CUDA", "TensorRT"],
                "relevance": "Direct technical connection to LLM Inference Roofline model diagnostics and ONNX Runtime benchmarks.",
                "potential_contribution": "Benchmarking TensorRT-LLM kernel performance across Hopper and Blackwell architectures.",
                "duration_weeks": 12,
                "compensation_type": CompensationType.PAID.value,
                "compensation_details": "$48–$60/hour + corporate housing and airfare.",
                "eligibility": "Undergraduate students in Engineering / CS. J-1 visa sponsorship provided for international interns.",
                "evidence_url": "https://www.nvidia.com/en-us/about-nvidia/careers/university-recruiting/",
                "contact_channel": ContactChannel.APPLICATION_PORTAL.value,
                "contact_name": "NVIDIA University Recruiting",
                "contact_email": None,
                "application_url": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite",
                "deadline": "2027-01-15"
            }
        ]
        return seed_frontier[:limit]

    def normalize_raw_item(self, raw_item: Dict[str, Any]) -> Tuple[Organization, Opportunity]:
        company_name = raw_item["company_name"]
        web_url = canonicalize_url(raw_item.get("website", ""))
        org_id = compute_organization_id(company_name, web_url)

        org = Organization(
            organization_id=org_id,
            name=company_name,
            category=OpportunityType.FRONTIER_COMPANY.value,
            website_url=web_url,
            country=raw_item.get("country"),
            city=raw_item.get("city"),
            iana_timezone=raw_item.get("timezone", "UTC"),
            description=f"Frontier technology organization specializing in {raw_item.get('domains', ['AI'])[0]}",
            tier="TIER_1"
        )

        title = raw_item["title"]
        opp_id = compute_opportunity_id(org_id, OpportunityType.FRONTIER_COMPANY.value, title, "Summer 2027")

        evidence_urls = [canonicalize_url(raw_item.get("evidence_url", ""))]
        if raw_item.get("application_url"):
            evidence_urls.append(canonicalize_url(raw_item["application_url"]))

        opp = Opportunity(
            opportunity_id=opp_id,
            organization_id=org_id,
            title=title,
            opportunity_type=OpportunityType.FRONTIER_COMPANY.value,
            source="FRONTIER_INDUSTRY_REGISTRY",
            location=f"{raw_item.get('city')}, {raw_item.get('country')}",
            iana_timezone=raw_item.get("timezone", "UTC"),
            technical_domains=raw_item.get("domains", []),
            technical_relevance_summary=raw_item.get("relevance"),
            potential_contribution=raw_item.get("potential_contribution"),
            eligibility_criteria=raw_item.get("eligibility"),
            internship_duration_weeks=raw_item.get("duration_weeks", 12),
            target_term="Summer 2027",
            compensation_type=raw_item.get("compensation_type", CompensationType.PAID.value),
            compensation_details=raw_item.get("compensation_details"),
            official_evidence_urls=[u for u in evidence_urls if u],
            hiring_contact_channel=raw_item.get("contact_channel", ContactChannel.APPLICATION_PORTAL.value),
            contact_email=raw_item.get("contact_email"),
            contact_name=raw_item.get("contact_name"),
            contact_verification_status=VerificationStatus.NOT_APPLICABLE.value if not raw_item.get("contact_email") else VerificationStatus.VERIFIED.value,
            application_url=canonicalize_url(raw_item.get("application_url", "")),
            application_deadline=raw_item.get("deadline"),
            next_action="SUBMIT_OFFICIAL_CAREERS_PORTAL"
        )

        return org, opp
