"""
Opportunity Intelligence Engine: Multi-Factor Qualification & Ranking Engine.

Scores each opportunity from 0.0 to 100.0 based on:
1. Technical Fit (max 35.0) - Alignment with verified projects (SciML, LLM inference, robotics, agents, PINNs).
2. Compensation & Financial Support (max 20.0) - Paid salary, stipends, international travel/living support.
3. Engineering Ownership & Impact (max 15.0) - Direct hands-on code, research implementation, system building.
4. Applicant Eligibility (max 15.0) - Undergraduate eligibility, international visa support, Summer 2027 duration (8-12 weeks).
5. Team Quality & Learning (max 10.0) - Tier-1 VCs, renowned labs/PIs, frontier tech density.
6. Source Grounding (max 5.0) - Official direct evidence URLs and verified channels.
"""

import re
from typing import Dict, Any, List, Optional, Tuple

from src.opportunities.models import (
    Opportunity,
    QualificationBreakdown,
    CompensationType,
    DiscoveryStatus,
    PriorityTier
)


class OpportunityRanker:
    """
    Ranks and qualifies opportunities against the applicant's verified engineering profile.
    """

    # Applicant's verified project keywords and corresponding project IDs
    TECH_MATCH_MATRIX = [
        {
            "project_id": "Neural Process Twin",
            "keywords": [
                "scientific ml", "sciml", "surrogate model", "surrogate", "physics-informed",
                "heat conduction", "pde", "computational simulation",
                "pytorch simulation", "neural operator", "fno", "pinns"
            ],
            "weight": 12.0
        },
        {
            "project_id": "LLM Inference Intelligence Engine",
            "keywords": [
                "llm inference", "inference optimization", "roofline model", "onnx runtime",
                "gpu kernel", "triton", "cuda", "vllm", "tensorrt", "memory-bandwidth",
                "high performance computing", "hpc", "quantization", "model serving", "latency"
            ],
            "weight": 12.0
        },
        {
            "project_id": "Finite-Difference PINN Error Estimation",
            "keywords": [
                "finite difference", "pinn error", "numerical error", "error estimation",
                "loss weighting", "pde residual", "gradient pathology", "discretization"
            ],
            "weight": 10.0
        },
        {
            "project_id": "Sovereign On-Premise Agentic AI Workbench",
            "keywords": [
                "agentic", "ai agents", "tool sandboxing", "autonomous agent", "multimodal llm",
                "rag", "langchain", "function calling", "agent infrastructure", "agentic workflows"
            ],
            "weight": 10.0
        },
        {
            "project_id": "Humanoid Robotics & SuRGE Microdrone",
            "keywords": [
                "robotics", "drone", "uav", "microdrone", "kinematics", "embodied intelligence",
                "control systems", "servo", "embedded c", "autonomous flight", "slam", "ros", "ros2",
                "bipedal", "humanoid", "actuator", "locomotion"
            ],
            "weight": 9.0
        },
        {
            "project_id": "Peng-Robinson Real-Gas Cubic Equation of State Solver",
            "keywords": [
                "c++17", "thermodynamics", "chemical engineering", "materials", "equation of state",
                "computational fluid dynamics", "cfd", "process simulation", "cryogenic", "phase equilibria"
            ],
            "weight": 8.0
        }
    ]

    @classmethod
    def score_opportunity(cls, opp: Opportunity, org: Optional[Any] = None) -> Opportunity:
        """Convenience method to score, update, and return an opportunity."""
        total, breakdown, discovery_status, matched_proj = cls.evaluate_opportunity(opp)
        opp.qualification_score = total
        opp.score_breakdown = breakdown
        opp.discovery_status = discovery_status
        if matched_proj:
            opp.matched_project_id = matched_proj
        return opp

    @classmethod
    def compute_priority_tier(cls, total_score: float, is_strictly_ineligible: bool = False) -> str:
        """Assign standardized priority tier."""
        if is_strictly_ineligible:
            return PriorityTier.INELIGIBLE.value
        if total_score >= 80.0:
            return PriorityTier.HIGH_PRIORITY.value
        elif total_score >= 65.0:
            return PriorityTier.PROMISING.value
        elif total_score >= 50.0:
            return PriorityTier.NEEDS_VERIFICATION.value
        else:
            return PriorityTier.REJECTED.value

    @classmethod
    def evaluate_opportunity(cls, opp: Opportunity) -> Tuple[float, QualificationBreakdown, str, Optional[str]]:
        """
        Evaluate and score an opportunity.
        Returns:
            (total_score, QualificationBreakdown, discovery_status, matched_project_id)
        """
        breakdown = QualificationBreakdown()

        # 1. Technical Fit (max 35.0)
        tech_score, matched_project = cls._score_technical_fit(opp)
        breakdown.technical_fit = tech_score

        # 2. Compensation & Financial Support (max 20.0)
        comp_score = cls._score_compensation(opp)
        breakdown.compensation = comp_score

        # 3. Engineering Ownership & Impact (max 15.0)
        ownership_score = cls._score_ownership(opp)
        breakdown.ownership_impact = ownership_score

        # 4. Applicant Eligibility (max 15.0)
        elig_score, is_strictly_ineligible = cls._score_eligibility(opp)
        breakdown.eligibility = elig_score

        # 5. Team Quality & Learning (max 10.0)
        team_score = cls._score_team_quality(opp)
        breakdown.learning_quality = team_score

        # 6. Source Grounding (max 5.0)
        grounding_score = cls._score_source_grounding(opp)
        breakdown.source_grounding = grounding_score

        total = breakdown.calculate_total()

        # Hard disqualification if explicitly ineligible (e.g. requires US Citizenship or PhD only)
        if is_strictly_ineligible:
            total = min(total, 45.0)
            discovery_status = DiscoveryStatus.DISQUALIFIED.value
        elif total >= 70.0:
            discovery_status = DiscoveryStatus.QUALIFIED.value
        elif total >= 50.0:
            discovery_status = DiscoveryStatus.NEEDS_REVIEW.value
        else:
            discovery_status = DiscoveryStatus.DISQUALIFIED.value

        tier = cls.compute_priority_tier(total, is_strictly_ineligible)
        opp.priority_tier = tier

        return total, breakdown, discovery_status, matched_project

    @classmethod
    def _score_technical_fit(cls, opp: Opportunity) -> Tuple[float, Optional[str]]:
        combined_text = " ".join([
            opp.title or "",
            opp.technical_relevance_summary or "",
            opp.potential_contribution or "",
            " ".join(opp.technical_domains or [])
        ]).lower()

        project_matches = []
        for item in cls.TECH_MATCH_MATRIX:
            matched_keywords = [kw for kw in item["keywords"] if kw in combined_text]
            if matched_keywords:
                score = min(25.0, len(matched_keywords) * 5.0)
                project_matches.append((score, item["project_id"]))

        if not project_matches:
            if any(w in combined_text for w in ["ai", "machine learning", "engineering", "software", "computational"]):
                return 10.0, "Neural Process Twin"
            return 0.0, None

        # Sort matches by score descending
        project_matches.sort(key=lambda x: x[0], reverse=True)
        best_score, best_project = project_matches[0]

        # Primary project contributes up to 25.0; secondary matches contribute up to 10.0
        secondary_score = sum(min(5.0, m[0] * 0.25) for m in project_matches[1:])
        total_tech_score = min(35.0, best_score + secondary_score)

        return round(total_tech_score, 1), best_project

    @classmethod
    def _score_compensation(cls, opp: Opportunity) -> float:
        comp_type = opp.compensation_type.upper()
        details = (opp.compensation_details or "").lower()

        if comp_type == CompensationType.PAID.value:
            return 20.0
        elif comp_type == CompensationType.STIPEND.value:
            if any(kw in details for kw in ["generous", "full", "travel", "housing", "accommodation"]):
                return 18.0
            return 15.0
        elif comp_type == CompensationType.TRAVEL_LIVING_SUPPORT.value:
            return 14.0
        elif comp_type == CompensationType.UNKNOWN.value:
            if any(kw in details for kw in ["funded", "stipend", "salary", "$", "eur", "chf", "inr"]):
                return 16.0
            return 8.0  # Default assumption for startups/companies before disclosure
        elif comp_type == CompensationType.UNPAID_ACADEMIC.value:
            return 0.0
        return 5.0

    @classmethod
    def _score_ownership(cls, opp: Opportunity) -> float:
        combined = " ".join([
            opp.title or "",
            opp.potential_contribution or "",
            opp.technical_relevance_summary or ""
        ]).lower()

        score = 8.0  # Base score for technical opportunities

        if any(w in combined for w in ["founding", "core engineer", "production", "ownership", "implement", "build from scratch"]):
            score += 7.0
        elif any(w in combined for w in ["research intern", "fellow", "hands-on", "benchmark", "develop"]):
            score += 4.0

        return round(min(15.0, score), 1)

    @classmethod
    def _score_eligibility(cls, opp: Opportunity) -> Tuple[float, bool]:
        elig_text = (opp.eligibility_criteria or "").lower()
        title_text = (opp.title or "").lower()

        # Check for strict ineligibility
        is_strictly_ineligible = False
        if any(kw in elig_text for kw in ["us citizenship required", "security clearance required", "must be us citizen"]):
            is_strictly_ineligible = True
        if "phd only" in elig_text or "must be a phd student" in elig_text:
            is_strictly_ineligible = True

        if is_strictly_ineligible:
            return 0.0, True

        score = 0.0

        # Undergraduate eligibility
        if any(kw in elig_text for kw in ["undergraduate", "bachelor", "b.tech", "all degree levels", "open to all"]):
            score += 5.0
        elif not any(kw in elig_text for kw in ["phd", "postdoc"]):
            score += 4.0  # Default unless restricted

        # International / Visa support
        if any(kw in elig_text for kw in ["international students", "visa sponsorship", "j-1", "all nationalities", "worldwide"]):
            score += 5.0
        elif not any(kw in elig_text for kw in ["citizens only", "permanent residents only"]):
            score += 3.0

        # Duration & Summer 2027 timeline
        duration = opp.internship_duration_weeks
        if (duration is not None and 8 <= duration <= 14) or "summer 2027" in (opp.target_term or "").lower():
            score += 5.0
        else:
            score += 2.0

        return round(min(15.0, score), 1), False

    @classmethod
    def _score_team_quality(cls, opp: Opportunity) -> float:
        text = " ".join([
            opp.title or "",
            opp.technical_relevance_summary or "",
            opp.potential_contribution or "",
            opp.eligibility_criteria or "",
            opp.source or ""
        ]).lower()

        # Top research institutions & Tier-1 companies
        if any(top in text for top in ["stanford", "mit", "oxford", "ethz", "cern", "max planck", "anthropic", "openai", "deepmind", "nvidia", "google", "meta"]):
            return 10.0

        # High-caliber venture-backed frontier startups
        if opp.opportunity_type == "STARTUP":
            return 8.0

        if any(k in text for k in ["yc", "series a", "series b", "inria", "tum", "epfl"]):
            return 8.0

        return 7.0

    @classmethod
    def _score_source_grounding(cls, opp: Opportunity) -> float:
        urls = opp.official_evidence_urls or []
        if not urls:
            return 0.0

        score = 3.0  # Has at least one evidence URL

        if opp.application_url and opp.application_url.startswith("https://"):
            score += 1.0

        if opp.contact_email and "@" in opp.contact_email:
            score += 1.0

        return round(min(5.0, score), 1)
