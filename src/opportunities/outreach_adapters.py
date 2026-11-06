"""
Opportunity Intelligence Engine: Category-Specific Outbound Outreach Strategy Adapters.

Implements dedicated strategy adapters for the 5 opportunity categories:
1. AcademicOutreachStrategyAdapter (Category 1: Academic Labs & Faculty)
2. StartupOutreachStrategyAdapter (Category 2: Frontier Startups with Hybrid Routing A/B/C/D)
3. FrontierIndustryStrategyAdapter (Category 3: Frontier Industry R&D & Tech Companies)
4. InstitutionalInquiryStrategyAdapter (Category 4 & 5: Public Labs, National Centers & Fellowships)
5. SpecialistOrganizationStrategyAdapter (Category 6: AI Nonprofits, Open-Source Foundations, Incubators)

Enforces strict ApplicantTruthLock:
- Grounded in Harshith N's verified IIT Delhi profile and 7 canonical projects.
- Explicitly differentiates interest/willingness from prior completed experience.
- Never overstates accomplishments (no custom CUDA kernels, no multi-phase distillation flash solvers).
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List

from src.opportunities.models import Opportunity, Organization, OpportunityType
from src.opportunities.applicant_truth_lock import ApplicantTruthLock

logger = logging.getLogger(__name__)


class BaseOutreachStrategyAdapter(ABC):
    """Abstract base class for opportunity category outreach adapters."""

    @abstractmethod
    def get_category_name(self) -> str:
        """Returns the category name."""
        pass

    @abstractmethod
    def generate_outreach_payload(self, opp: Opportunity, org: Optional[Organization] = None) -> Dict[str, Any]:
        """Generates validated, evidence-grounded outreach subject and body."""
        pass


class AcademicOutreachStrategyAdapter(BaseOutreachStrategyAdapter):
    """
    Category 1: Academic Research Laboratories & Professors.
    Focuses on lab research papers, scientific overlap, specific technical alignment,
    and Summer 2027 visiting undergraduate research feasibility.
    """

    def get_category_name(self) -> str:
        return "ACADEMIC_LAB"

    def generate_outreach_payload(self, opp: Opportunity, org: Optional[Organization] = None) -> Dict[str, Any]:
        prof_name = opp.contact_name or "Professor"
        uni_name = org.name if org else (opp.location or "your university")
        matched_proj = opp.matched_project_id or "neural-process-twin"

        # Map candidate project evidence safely
        if "llm" in matched_proj or "infer" in matched_proj:
            proj_mention = (
                "At IIT Delhi, I developed an LLM inference profiling tool investigating TTFT, "
                "inter-token latency, and Roofline memory-bandwidth bottlenecks across CPU/GPU execution backends."
            )
            interest_mention = (
                f"I am keenly interested in your group's work on systems for deep learning and inference infrastructure, "
                f"and I would welcome the opportunity to contribute to empirical benchmarking and runtime evaluation."
            )
        elif "thermo" in matched_proj or "cpp" in matched_proj or "eos" in matched_proj:
            proj_mention = (
                "In my coursework and project work at IIT Delhi (CHL2002), I implemented modular C++17 software "
                "for real-gas pressure-volume-temperature calculations using the Peng-Robinson cubic equation of state, "
                "extracting physical roots and evaluating analytical thermodynamic stability derivatives (dP/dv)_T."
            )
            interest_mention = (
                f"Given your laboratory's work in chemical engineering and process systems, "
                f"I would be thrilled to contribute to robust scientific computing or thermodynamic property routines."
            )
        else:
            proj_mention = (
                "At IIT Delhi, I implemented a learned surrogate simulation workflow in PyTorch (Neural Process Twin) "
                "to approximate 2D steady-state physical fields from numerical solver data, evaluating prediction accuracy "
                "and physical consistency across parameter and resolution variations."
            )
            interest_mention = (
                f"I am deeply inspired by your research in scientific machine learning and physical modeling. "
                f"While my prior work has focused on surrogate benchmarks, I am eager to expand into your laboratory's "
                f"advanced formulations during Summer 2027."
            )

        subject = f"Prospective Summer 2027 Research Collaboration – Harshith N (IIT Delhi)"
        body = (
            f"Dear Professor {prof_name},\n\n"
            f"I am a second-year undergraduate in Chemical Engineering at the Indian Institute of Technology Delhi "
            f"(IIT Delhi), writing to inquire about undergraduate research opportunities or visiting student positions "
            f"in your research group for Summer 2027 (10–12 weeks, May to July 2027).\n\n"
            f"{interest_mention}\n\n"
            f"{proj_mention}\n\n"
            f"I would be grateful for the chance to contribute to ongoing projects in your lab. "
            f"My curriculum vitae and project repositories are available at github.com/harsh-bitverse for your review. "
            f"Thank you very much for your time and consideration.\n\n"
            f"Sincerely,\n"
            f"Harshith N\n"
            f"Department of Chemical Engineering\n"
            f"Indian Institute of Technology Delhi\n"
            f"ch1251635@iitd.ac.in"
        )

        ApplicantTruthLock.assert_evidence_grounded(body)

        return {
            "strategy": "ACADEMIC_RESEARCH_INQUIRY",
            "subject": subject,
            "body": body,
            "channel": "DIRECT_FACULTY_EMAIL",
            "grounded_project_id": matched_proj,
            "requires_manual_authorization": True
        }


class StartupOutreachStrategyAdapter(BaseOutreachStrategyAdapter):
    """
    Category 2: Frontier Startups (Indian & International).
    Enforces the 4-route hybrid dispatch architecture:
    - ROUTE_A_OFFICIAL_PORTAL: Portal application only (no outbound email)
    - ROUTE_B_DIRECT_ENGINEERING_OUTREACH: Founder/engineering direct technical email
    - ROUTE_C_COORDINATED_PORTAL_PLUS_EMAIL: Portal submission supplemented by targeted email
    - ROUTE_D_MONITOR: Monitor careers page for future openings
    """

    def get_category_name(self) -> str:
        return "STARTUP"

    def generate_outreach_payload(self, opp: Opportunity, org: Optional[Organization] = None) -> Dict[str, Any]:
        route = opp.application_route or "ROUTE_B_DIRECT_ENGINEERING_OUTREACH"
        company_name = org.name if org else (opp.title or "your company")
        contact_name = opp.contact_name or "Engineering Team"

        if route == "ROUTE_A_OFFICIAL_PORTAL":
            return {
                "strategy": "ROUTE_A_PORTAL_ONLY",
                "route": route,
                "portal_url": opp.application_url or "",
                "subject": None,
                "body": None,
                "channel": "PORTAL_APPLICATION",
                "notes": "Route A designated: strictly portal-driven application. No outbound email dispatch permitted.",
                "requires_manual_authorization": False
            }

        if route == "ROUTE_D_MONITOR":
            return {
                "strategy": "ROUTE_D_MONITOR",
                "route": route,
                "portal_url": opp.application_url or "",
                "subject": None,
                "body": None,
                "channel": "MONITORING",
                "notes": "Route D designated: monitor company page for future internship openings.",
                "requires_manual_authorization": False
            }

        # Route B and Route C involve direct engineering outreach
        subject = f"Summer 2027 Engineering Internship – Harshith N (IIT Delhi)"
        body = (
            f"Hi {contact_name},\n\n"
            f"I am an undergraduate at IIT Delhi (B.Tech Chemical Engineering, Class of 2029) writing to express "
            f"strong interest in contributing to {company_name}'s engineering and systems work as an intern for Summer 2027.\n\n"
            f"I focus on practical systems implementation and performance analysis. In my recent work, I built an "
            f"end-to-end LLM inference evaluation tool measuring TTFT, inter-token latency, and memory across quantization "
            f"levels and execution backends (ONNX Runtime, PyTorch), using Roofline modeling to isolate memory-bandwidth "
            f"bounds from compute bottlenecks.\n\n"
            f"I admire {company_name}'s focus on production efficiency, and I would love to tackle high-throughput "
            f"instrumentation, benchmarking, or systems engineering challenges with your team for 8–12 weeks.\n\n"
            f"My technical background and projects are documented at github.com/harsh-bitverse. I would be thrilled "
            f"to speak briefly if there might be a fit for Summer 2027.\n\n"
            f"Best regards,\n"
            f"Harshith N\n"
            f"IIT Delhi | ch1251635@iitd.ac.in"
        )

        ApplicantTruthLock.assert_evidence_grounded(body)

        return {
            "strategy": "STARTUP_DIRECT_ENGINEERING_PITCH",
            "route": route,
            "subject": subject,
            "body": body,
            "channel": "DIRECT_FOUNDER_OR_ENGINEERING_EMAIL",
            "requires_manual_authorization": True
        }


class FrontierIndustryStrategyAdapter(BaseOutreachStrategyAdapter):
    """
    Category 3: Industrial R&D and Frontier Technology Companies.
    Focuses on applied research benchmarks, production engineering, scientific ML,
    and enterprise research group alignment.
    """

    def get_category_name(self) -> str:
        return "FRONTIER_COMPANY"

    def generate_outreach_payload(self, opp: Opportunity, org: Optional[Organization] = None) -> Dict[str, Any]:
        company_name = org.name if org else (opp.title or "your organization")
        contact_name = opp.contact_name or "Campus Talent & Research Team"

        subject = f"Summer 2027 Research & Systems Internship Inquiry – Harshith N (IIT Delhi)"
        body = (
            f"Dear {contact_name},\n\n"
            f"I am a second-year undergraduate at the Indian Institute of Technology Delhi (IIT Delhi), "
            f"writing to inquire about research and systems engineering internship opportunities at {company_name} "
            f"for Summer 2027.\n\n"
            f"My technical background bridges computational science and AI systems. I have developed learned "
            f"surrogate simulation pipelines (Neural Process Twin) in PyTorch to approximate steady-state physical fields "
            f"from numerical solver outputs, and benchmarked LLM inference efficiency across memory-constrained hardware "
            f"using Roofline diagnostics.\n\n"
            f"I am eager to contribute to {company_name}'s applied computing and simulation efforts. "
            f"My CV and verified codebases are available at github.com/harsh-bitverse for review.\n\n"
            f"Thank you for your consideration.\n\n"
            f"Sincerely,\n"
            f"Harshith N\n"
            f"IIT Delhi | ch1251635@iitd.ac.in"
        )

        ApplicantTruthLock.assert_evidence_grounded(body)

        return {
            "strategy": "INDUSTRIAL_RD_INQUIRY",
            "subject": subject,
            "body": body,
            "channel": "CAREERS_OR_RESEARCH_EMAIL",
            "requires_manual_authorization": True
        }


class InstitutionalInquiryStrategyAdapter(BaseOutreachStrategyAdapter):
    """
    Category 4 & 5: Public Laboratories, National Research Centers, and Summer Fellowships.
    Focuses on formal program eligibility (DAAD, CERN, Mitacs), institutional guidelines,
    undergraduate visiting schemes, and faculty mentor matching.
    """

    def get_category_name(self) -> str:
        return "INSTITUTIONAL_PROGRAM"

    def generate_outreach_payload(self, opp: Opportunity, org: Optional[Organization] = None) -> Dict[str, Any]:
        program_name = opp.title or (org.name if org else "Research Programme")
        contact_name = opp.contact_name or "Program Coordinator"

        subject = f"Inquiry Regarding Summer 2027 Visiting Fellowship – {program_name} – Harshith N"
        body = (
            f"Dear {contact_name},\n\n"
            f"I am an undergraduate student in Chemical Engineering at the Indian Institute of Technology Delhi (IIT Delhi), "
            f"writing to inquire regarding application procedures and eligibility guidelines for {program_name} "
            f"for the Summer 2027 session.\n\n"
            f"My academic and research preparation includes coursework in thermodynamics, transport phenomena, "
            f"and numerical computing, alongside independent implementations in scientific machine learning (PINNs) "
            f"and modular C++17 equation-of-state modeling.\n\n"
            f"I would appreciate any guidance regarding application deadlines, mentor matching, or formal institutional "
            f"visiting student protocols for Summer 2027.\n\n"
            f"Thank you very much for your assistance.\n\n"
            f"Respectfully,\n"
            f"Harshith N\n"
            f"IIT Delhi | ch1251635@iitd.ac.in"
        )

        ApplicantTruthLock.assert_evidence_grounded(body)

        return {
            "strategy": "INSTITUTIONAL_PROGRAM_INQUIRY",
            "subject": subject,
            "body": body,
            "channel": "PROGRAM_ADMIN_EMAIL",
            "requires_manual_authorization": True
        }


class SpecialistOrganizationStrategyAdapter(BaseOutreachStrategyAdapter):
    """
    Category 6: Other Exceptional Opportunities (Nonprofit AI Labs, Open-Source Foundations, Robotics Institutes).
    """

    def get_category_name(self) -> str:
        return "OTHER_ORGANIZATION"

    def generate_outreach_payload(self, opp: Opportunity, org: Optional[Organization] = None) -> Dict[str, Any]:
        org_name = org.name if org else "your organization"
        contact_name = opp.contact_name or "Team"

        subject = f"Summer 2027 Collaboration Inquiry – Harshith N (IIT Delhi)"
        body = (
            f"Dear {contact_name},\n\n"
            f"I am writing from the Indian Institute of Technology Delhi (IIT Delhi) to explore research collaboration "
            f"or technical internship opportunities with {org_name} during Summer 2027.\n\n"
            f"My work focuses on open-source scientific machine learning and AI inference systems, with verified projects "
            f"in surrogate physical simulation and LLM runtime evaluation. I am eager to contribute to {org_name}'s mission.\n\n"
            f"My CV and code repositories are accessible at github.com/harsh-bitverse.\n\n"
            f"Sincerely,\n"
            f"Harshith N\n"
            f"IIT Delhi | ch1251635@iitd.ac.in"
        )

        ApplicantTruthLock.assert_evidence_grounded(body)

        return {
            "strategy": "SPECIALIST_ORGANIZATION_INQUIRY",
            "subject": subject,
            "body": body,
            "channel": "COMMUNITY_OR_FOUNDATION_EMAIL",
            "requires_manual_authorization": True
        }


class OutreachStrategyRegistry:
    """Registry providing the appropriate category strategy adapter."""

    _adapters: Dict[str, BaseOutreachStrategyAdapter] = {
        OpportunityType.ACADEMIC_LAB.value: AcademicOutreachStrategyAdapter(),
        OpportunityType.STARTUP.value: StartupOutreachStrategyAdapter(),
        OpportunityType.FRONTIER_COMPANY.value: FrontierIndustryStrategyAdapter(),
        OpportunityType.INSTITUTIONAL_PROGRAM.value: InstitutionalInquiryStrategyAdapter(),
        "OTHER_ORGANIZATION": SpecialistOrganizationStrategyAdapter()
    }

    @classmethod
    def get_adapter(cls, category: str) -> BaseOutreachStrategyAdapter:
        return cls._adapters.get(category, cls._adapters["OTHER_ORGANIZATION"])
