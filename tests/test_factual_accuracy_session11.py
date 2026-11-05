"""
Session 11: Applicant and Recipient Factual Accuracy Verification Tests (Workstream F).

Validates that all generated personalization drafts and outreach packets strictly adhere to:
1. Student identity: Harshith N, Department of Chemical Engineering, IIT Delhi (ch1251635@iitd.ac.in).
2. Exactly 6 verified projects:
   - Neural Process Twin
   - LLM Inference Intelligence Engine
   - PINN Error Estimation term paper
   - C++ Peng–Robinson EOS solver
   - SuRGE microdrone work
   - Agentic Industrial AI Workbench
3. Zero fabricated metrics:
   - No fabricated speedups ("12x", "4.2x", "100x")
   - No AMG implementation claims
   - No SIMD/flash speedup fabrications
   - No commercial drone flight hardware claims
4. Authentic domain adaptation across all 4 categories.
"""

import re
import pytest
from src.opportunities.models import Opportunity, Organization, OpportunityType, ContactChannel
from src.opportunities.personalization import DomainAdaptivePersonalizer


@pytest.fixture
def mock_orgs_and_opps():
    """Returns sample opportunities across all 4 categories."""
    academic_org = Organization(
        organization_id="org_acad_1",
        name="UC Berkeley — Sky Computing Lab",
        category="ACADEMIC_LAB",
        website_url="https://sky.cs.berkeley.edu",
        iana_timezone="America/Los_Angeles"
    )
    academic_opp = Opportunity(
        opportunity_id="opp_acad_1",
        organization_id="org_acad_1",
        opportunity_type=OpportunityType.ACADEMIC_LAB.value,
        title="Visiting Research Intern — Distributed LLM Systems",
        source="SESSION_11",
        contact_name="Prof. Matei Zaharia",
        contact_email="matei@cs.berkeley.edu",
        hiring_contact_channel=ContactChannel.FACULTY_PI.value,
        technical_relevance_summary="Distributed ML serving, vLLM architecture, and low-latency KV-cache memory management.",
        potential_contribution="Benchmarking token latency and throughput across distributed model serving backends under dynamic batching.",
        matched_project_id="LLM Inference Intelligence Engine",
        official_evidence_urls=["https://sky.cs.berkeley.edu"]
    )

    startup_org = Organization(
        organization_id="org_start_1",
        name="Etched",
        category="STARTUP",
        website_url="https://etched.com",
        iana_timezone="America/Los_Angeles"
    )
    startup_opp = Opportunity(
        opportunity_id="opp_start_1",
        organization_id="org_start_1",
        opportunity_type=OpportunityType.STARTUP.value,
        title="Inference Systems Intern — Summer 2027",
        source="SESSION_11",
        contact_name="Etched Architecture Lead",
        contact_email="careers@etched.com",
        hiring_contact_channel=ContactChannel.EMAIL.value,
        technical_relevance_summary="Specialized ASIC hardware for transformer inference.",
        potential_contribution="Formulating Roofline intensity benchmarks and profiling memory-bandwidth saturation.",
        matched_project_id="LLM Inference Intelligence Engine",
        official_evidence_urls=["https://etched.com"]
    )

    industry_org = Organization(
        organization_id="org_ind_1",
        name="Google DeepMind",
        category="FRONTIER_COMPANY",
        website_url="https://deepmind.google",
        iana_timezone="Europe/London"
    )
    industry_opp = Opportunity(
        opportunity_id="opp_ind_1",
        organization_id="org_ind_1",
        opportunity_type=OpportunityType.FRONTIER_COMPANY.value,
        title="AI for Science Research Intern",
        source="SESSION_11",
        hiring_contact_channel=ContactChannel.APPLICATION_PORTAL.value,
        application_url="https://deepmind.google/careers",
        technical_relevance_summary="Learned simulation surrogates for physical fields and PDEs.",
        potential_contribution="Evaluating neural surrogate prediction error against numerical-solver outputs.",
        matched_project_id="Neural Process Twin",
        official_evidence_urls=["https://deepmind.google"]
    )

    inst_org = Organization(
        organization_id="org_inst_1",
        name="CERN",
        category="INSTITUTIONAL_PROGRAM",
        website_url="https://openlab.cern",
        iana_timezone="Europe/Zurich"
    )
    inst_opp = Opportunity(
        opportunity_id="opp_inst_1",
        organization_id="org_inst_1",
        opportunity_type=OpportunityType.INSTITUTIONAL_PROGRAM.value,
        title="CERN openlab Summer Student",
        source="SESSION_11",
        hiring_contact_channel=ContactChannel.APPLICATION_PORTAL.value,
        application_url="https://openlab.cern/summer-student-programme",
        technical_relevance_summary="High-performance computing and heterogeneous architectures for simulation.",
        potential_contribution="Benchmarking compute ceilings and memory bandwidth across CPU/GPU architectures.",
        matched_project_id="Distributed Systems / GPU Kernel Optimization / HPC",
        official_evidence_urls=["https://openlab.cern"]
    )

    return {
        "academic": (academic_opp, academic_org),
        "startup": (startup_opp, startup_org),
        "industry": (industry_opp, industry_org),
        "institution": (inst_opp, inst_org)
    }


def test_applicant_profile_grounding():
    """Verify applicant profile constants strictly align with verified credentials."""
    profile = DomainAdaptivePersonalizer.APPLICANT_PROFILE
    assert profile["full_name"] == "Harshith N"
    assert profile["institution"] == "Indian Institute of Technology Delhi (IIT Delhi)"
    assert profile["major"] == "Chemical Engineering"
    assert profile["email"] == "ch1251635@iitd.ac.in"
    assert profile["portfolio_url"] == "github.com/harsh-bitverse"

    projects = profile["verified_projects"]
    assert len(projects) == 6
    expected_projects = {
        "Neural Process Twin",
        "LLM Inference Intelligence Engine",
        "PINN Error Estimation term paper",
        "C++ Peng–Robinson EOS solver",
        "SuRGE microdrone work",
        "Agentic Industrial AI Workbench"
    }
    assert set(projects.keys()) == expected_projects


def test_prohibited_fabrications_check(mock_orgs_and_opps):
    """Verify generated drafts contain ZERO fabricated speedups, AMG claims, or hardware claims."""
    opps = mock_orgs_and_opps

    for cat_name, (opp, org) in opps.items():
        if cat_name == "startup":
            draft = DomainAdaptivePersonalizer.generate_startup_pitch(opp, org)
        elif cat_name == "academic":
            draft = DomainAdaptivePersonalizer.generate_academic_outreach(opp, org)
        elif cat_name == "industry":
            draft = DomainAdaptivePersonalizer.generate_industry_packet(opp, org)
        else:
            draft = DomainAdaptivePersonalizer.generate_institutional_dossier(opp, org)

        full_text = " ".join(str(v) for v in draft.values()).lower()

        # 1. Zero speedup fabrications
        speedup_patterns = [r"\b\d+x\s+speedup\b", r"\b\d+x\s+faster\b", r"\b100x\b", r"\b12x\b", r"\b4\.2x\b"]
        for pat in speedup_patterns:
            matches = re.findall(pat, full_text)
            assert not matches, f"Fabricated speedup pattern '{pat}' found in {cat_name} draft: {matches}"

        # 2. Zero AMG claims
        assert "algebraic multigrid" not in full_text
        assert "amg solver" not in full_text

        # 3. Zero commercial drone flight hardware
        assert "commercial drone flight" not in full_text
        assert "px4 commercial payload" not in full_text

        # 4. Must cite verified applicant identity
        assert "harshith n" in full_text
        assert "chemical engineering" in full_text
        assert "iit delhi" in full_text
