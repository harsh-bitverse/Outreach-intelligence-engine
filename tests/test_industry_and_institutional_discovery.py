"""
Test suite for Opportunity Intelligence Expansion — Session 04:
Frontier Industry Discovery, Institutional Programs & Fellowships, Academic Lab Top 120-250 Expansion Interface,
Global Portfolio Capacity Framework (8,000 Target), and Multi-Category Personalization.
"""

import os
import subprocess
import sqlite3
import pytest
from pathlib import Path

from config.settings import settings
from src.opportunities.models import (
    Opportunity,
    Organization,
    OpportunityType,
    OpportunityClassification,
    RoleTitleType,
    InternationalEligibility,
    RequisitionType,
    PortfolioTier
)
from src.opportunities.repository import OpportunityRepository
from src.opportunities.adapters.frontier_industry import FrontierIndustryAdapter
from src.opportunities.adapters.institutional_program import InstitutionalProgramAdapter
from src.opportunities.adapters.academic_lab import AcademicLabAdapter, ACADEMIC_EXPANSION_REGISTRY
from src.opportunities.data.frontier_industry_data import FRONTIER_INDUSTRY_DATA
from src.opportunities.data.institutional_programs_data import INSTITUTIONAL_PROGRAMS_DATA
from src.opportunities.portfolio_config import PortfolioCapacityConfig, QualityGateTier
from src.opportunities.dashboard import PortfolioDashboard
from src.opportunities.personalization import DomainAdaptivePersonalizer


@pytest.fixture
def dev_db_conn():
    dev_db_path = settings.DATA_DIR / "development" / "opportunity_dev.db"
    assert dev_db_path.exists(), "Development DB must exist"
    conn = sqlite3.connect(str(dev_db_path))
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def test_frontier_industry_adapter_catalog_and_fields():
    """
    Tests FrontierIndustryAdapter ingestion, normalization, statutory pay transparency,
    and ITAR defense restrictions.
    """
    adapter = FrontierIndustryAdapter()
    assert len(FRONTIER_INDUSTRY_DATA) >= 20, "Must have at least 20 frontier industry organizations"
    
    raw_items = adapter.fetch_raw_opportunities(limit=50)
    assert len(raw_items) >= 20
    
    spacex_item = next((i for i in raw_items if i["company_name"] == "SpaceX"), None)
    assert spacex_item is not None
    assert spacex_item["audit_classification"] == OpportunityClassification.INELIGIBLE.value
    assert spacex_item["international_eligibility"] == InternationalEligibility.RESTRICTED_DOMESTIC_ONLY.value
    
    nvidia_item = next((i for i in raw_items if "Nvidia" in i["company_name"]), None)
    assert nvidia_item is not None
    assert nvidia_item["audit_classification"] == OpportunityClassification.CONFIRMED_OPENING.value
    assert "SB 1162" in nvidia_item["compensation_details"]
    assert nvidia_item["compensation_evidence"] == "CALIFORNIA_SB_1162_STATUTORY_DISCLOSURE"
    
    org, opp = adapter.normalize_raw_item(nvidia_item)
    assert opp.requisition_type == RequisitionType.UNDERGRADUATE_INTERNSHIP.value
    assert opp.portfolio_tier == PortfolioTier.TIER_A_ELITE_PRIORITY.value
    assert opp.opportunity_type == OpportunityType.FRONTIER_COMPANY.value
    assert opp.is_confirmed_opening is True


def test_institutional_program_adapter_catalog_and_fields():
    """
    Tests InstitutionalProgramAdapter ingestion, fellowship stipend details,
    housing/travel support, and required document tracking.
    """
    adapter = InstitutionalProgramAdapter()
    assert len(INSTITUTIONAL_PROGRAMS_DATA) >= 20, "Must have at least 20 verified public research programs"
    
    raw_items = adapter.fetch_raw_opportunities(limit=50)
    assert len(raw_items) >= 20
    
    cern_item = next((i for i in raw_items if "CERN" in i["institution_name"]), None)
    assert cern_item is not None
    assert cern_item["stipend_amount"] == "CHF 90/day (~CHF 2,700/month)"
    assert cern_item["travel_support"] is not None
    assert cern_item["housing_support"] is not None
    assert cern_item["required_documents"] is not None
    
    daad_item = next((i for i in raw_items if "DAAD" in i["institution_name"]), None)
    assert daad_item is not None
    assert "€934" in daad_item["stipend_amount"]
    assert daad_item["matched_project"] == "PINN Error Estimation term paper"
    
    org, opp = adapter.normalize_raw_item(cern_item)
    assert opp.requisition_type == RequisitionType.FELLOWSHIP_PROGRAM.value
    assert opp.portfolio_tier == PortfolioTier.TIER_A_ELITE_PRIORITY.value
    assert opp.opportunity_type == OpportunityType.INSTITUTIONAL_PROGRAM.value
    assert opp.stipend_amount == "CHF 90/day (~CHF 2,700/month)"
    assert opp.is_confirmed_opening is True


def test_academic_lab_expansion_interface():
    """
    Tests AcademicLabAdapter Top 120-250 Global Universities expansion registry,
    capacity projection towards 3,000 academic target, and read-only production exclusion gating.
    """
    adapter = AcademicLabAdapter()
    universities = adapter.get_expansion_target_universities()
    assert len(universities) >= 15, "Expansion registry must include Top 120-250 universities"
    
    uni_names = [u["institution"] for u in universities]
    assert any("Purdue" in name for name in uni_names)
    assert any("Illinois" in name for name in uni_names)
    assert any("Technical University of Munich" in name for name in uni_names)
    assert any("Tokyo" in name for name in uni_names)
    assert any("KAIST" in name for name in uni_names)
    
    proj = adapter.get_academic_capacity_projection()
    assert proj["target_academic_portfolio_capacity"] == 3000
    assert proj["estimated_expansion_faculty_capacity"] > 500
    assert "Chemical Engineering" in proj["primary_departments"]
    
    exclusions = adapter.get_known_professor_exclusions()
    assert isinstance(exclusions, set)


def test_portfolio_capacity_framework_and_dashboard(dev_db_conn):
    """
    Tests PortfolioCapacityConfig target allocations (8,000 global target)
    and PortfolioDashboard calculation over database records.
    """
    assert PortfolioCapacityConfig.GLOBAL_TARGET == 8000
    assert PortfolioCapacityConfig.ACADEMIC_TARGET == 3000
    assert PortfolioCapacityConfig.STARTUP_TARGET_MIN == 2500
    assert PortfolioCapacityConfig.STARTUP_TARGET_MAX == 3000
    assert PortfolioCapacityConfig.INDUSTRY_TARGET_MIN == 1500
    assert PortfolioCapacityConfig.INSTITUTIONAL_TARGET_MIN == 500
    
    metrics = PortfolioDashboard.compute_portfolio_metrics(dev_db_conn)
    assert "total_opportunities_discovered" in metrics
    assert metrics["total_opportunities_discovered"] >= 133
    assert "gap_to_global_target" in metrics
    assert metrics["gap_to_global_target"] <= 8000
    
    md_report = PortfolioDashboard.render_markdown_dashboard(metrics)
    assert "# Global Summer 2027 Opportunity Portfolio Dashboard" in md_report
    assert "Target Capacity: **8,000**" in md_report
    assert "Quality Tier Distribution" in md_report


def test_domain_adaptive_personalization_multi_category():
    """
    Tests multi-category personalization workflows for industry, industrial research,
    and public institutional fellowships, asserting authoritative applicant identity.
    """
    profile = DomainAdaptivePersonalizer.APPLICANT_PROFILE
    assert profile["full_name"] == "Harshith N"
    assert "IIT Delhi" in profile["institution"]
    assert profile["major"] == "Chemical Engineering"
    
    # 1. Industry Engineering Internship Pitch
    org_ind = Organization(organization_id="org_nvidia", name="Nvidia Corporation", category=OpportunityType.FRONTIER_COMPANY.value)
    opp_ind = Opportunity(
        opportunity_id="opp_nvidia_1",
        organization_id="org_nvidia",
        title="GPU Tensor RT Model Serving Intern",
        opportunity_type=OpportunityType.FRONTIER_COMPANY.value,
        source="FRONTIER_INDUSTRY_REGISTRY",
        technical_domains=["GPU Systems", "Inference Serving"],
        technical_relevance_summary="low-latency inference serving",
        potential_contribution="benchmarking Roofline saturation and token latency",
        matched_project_id="LLM Inference Intelligence Engine"
    )
    pitch_ind = DomainAdaptivePersonalizer.generate_industry_pitch(opp_ind, org_ind)
    assert pitch_ind["applicant_name"] == "Harshith N"
    assert pitch_ind["tone"] == "PRODUCTION_ENGINEERING_CONTRIBUTION"
    assert "LLM Inference Intelligence Engine" in pitch_ind["pitch_subject"]
    assert "Roofline" in pitch_ind["pitch_body"]
    
    # 2. Industrial Research Lab Pitch
    org_res = Organization(organization_id="org_msr", name="Microsoft Research", category="INDUSTRIAL_RESEARCH_LAB")
    opp_res = Opportunity(
        opportunity_id="opp_msr_1",
        organization_id="org_msr",
        title="Scientific ML & AI for Science Research Intern",
        opportunity_type="INDUSTRIAL_RESEARCH_LAB",
        source="FRONTIER_INDUSTRY_REGISTRY",
        technical_domains=["Scientific ML", "PINNs"],
        technical_relevance_summary="physics-constrained neural operators",
        potential_contribution="investigating neural operator error bounds and PDE residual loss weighting",
        matched_project_id="PINN Error Estimation term paper"
    )
    pitch_res = DomainAdaptivePersonalizer.generate_industrial_research_pitch(opp_res, org_res)
    assert pitch_res["applicant_name"] == "Harshith N"
    assert pitch_res["tone"] == "SCIENTIFIC_RESEARCH_COLLABORATION"
    assert "Chemical Engineering" in pitch_res["pitch_body"]
    
    # 3. Institutional Fellowship Statement of Purpose
    org_cern = Organization(organization_id="org_cern", name="CERN", category=OpportunityType.INSTITUTIONAL_PROGRAM.value)
    opp_cern = Opportunity(
        opportunity_id="opp_cern_1",
        organization_id="org_cern",
        title="CERN openlab Summer Student Fellow",
        opportunity_type=OpportunityType.INSTITUTIONAL_PROGRAM.value,
        source="INSTITUTIONAL_PROGRAM_REGISTRY",
        internship_duration_weeks=9,
        stipend_amount="CHF 90/day (~CHF 2,700/month)",
        technical_domains=["HPC", "Scientific Computing"],
        matched_project_id="LLM Inference Intelligence Engine"
    )
    sop_cern = DomainAdaptivePersonalizer.generate_institutional_application(opp_cern, org_cern)
    assert sop_cern["applicant_name"] == "Harshith N"
    assert sop_cern["format"] == "FELLOWSHIP_STATEMENT_OF_PURPOSE"
    assert "STATEMENT OF PURPOSE" in sop_cern["statement_of_purpose"]
    assert "Harshith N" in sop_cern["statement_of_purpose"]


def test_production_isolation_and_pid_protection():
    """
    Guarantees production professor outreach process is undisturbed,
    and production SQLite database has zero modifications or opportunity tables.
    """
    cmd = ["powershell", "-NoProfile", "-Command", "Get-Process -Id 5916, 11048 | Select-Object -ExpandProperty Id"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"Get-Process failed: {res.stderr}"
    pids = [int(p) for p in res.stdout.strip().split() if p.isdigit()]
    assert len(pids) > 0, "Production outreach process must be running"
    
    prod_db_path = settings.DATA_DIR / "outreach_engine.db"
    assert prod_db_path.exists(), "Production DB must exist"
    
    # Open production DB in read-only mode to verify zero opportunity leakage
    conn = sqlite3.connect(f"file:{prod_db_path}?mode=ro", uri=True)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [r[0] for r in cur.fetchall()]
    conn.close()
    
    assert "professors" in tables
    assert "outbound_delivery_tracker" in tables
    assert "opportunities" not in tables, "Zero opportunity tables must exist in production DB"
    assert "discovery_checkpoints" not in tables, "Zero checkpoint tables must exist in production DB"
