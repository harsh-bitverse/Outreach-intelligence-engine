"""
Opportunity Intelligence Engine: Strategic Portfolio Capacity & Allocation Architecture.
Governs the continuous multi-session expansion toward an elite portfolio of approximately 8,000 global opportunities.

Capacity Targets (Flexible Configuration Parameters):
- GLOBAL_TARGET: 8,000 opportunities
- ACADEMIC_TARGET: ~3,000 professors & research groups (expanding QS top-120 to top-250)
- STARTUP_TARGET: ~2,500 - 3,000 technically ambitious frontier startups
- OTHER_TARGET: ~2,000 - 2,500 frontier industry & national laboratory research programs
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional
from enum import Enum


class PortfolioTierLevel(str, Enum):
    TIER_A_ELITE_PRIORITY = "TIER_A_ELITE_PRIORITY"
    TIER_B_STRONG_MATCH = "TIER_B_STRONG_MATCH"
    TIER_C_RESEARCH_PIPELINE = "TIER_C_RESEARCH_PIPELINE"


# Alias for backwards compatibility
QualityGateTier = PortfolioTierLevel


@dataclass
class PortfolioCapacityConfig:
    GLOBAL_TARGET: int = 8000
    ACADEMIC_TARGET: int = 3000
    STARTUP_TARGET_MIN: int = 2500
    STARTUP_TARGET_MAX: int = 3000
    INDUSTRY_TARGET_MIN: int = 1500
    INSTITUTIONAL_TARGET_MIN: int = 500
    OTHER_TARGET_MIN: int = 2000
    OTHER_TARGET_MAX: int = 2500

    global_target: int = 8000
    academic_target: int = 3000
    startup_target_min: int = 2500
    startup_target_max: int = 3000
    other_target_min: int = 2000
    other_target_max: int = 2500

    tier_a_min_score: float = 80.0
    tier_b_min_score: float = 55.0
    tier_c_min_score: float = 40.0

    def get_category_target(self, category: str) -> int:
        cat_upper = category.upper()
        if "ACADEMIC" in cat_upper or "PROFESSOR" in cat_upper:
            return self.academic_target
        elif "STARTUP" in cat_upper:
            return self.startup_target_max
        elif "FRONTIER" in cat_upper or "INDUSTRY" in cat_upper or "INSTITUTION" in cat_upper:
            return self.other_target_max
        return 1000

    def calculate_category_gap(self, category: str, current_count: int) -> int:
        target = self.get_category_target(category)
        return max(0, target - current_count)

    def determine_portfolio_tier(self, score: float, is_ineligible: bool = False, has_verified_contact: bool = False) -> str:
        if is_ineligible or score < self.tier_c_min_score:
            return PortfolioTierLevel.TIER_C_RESEARCH_PIPELINE.value
        if score >= self.tier_a_min_score and has_verified_contact:
            return PortfolioTierLevel.TIER_A_ELITE_PRIORITY.value
        elif score >= self.tier_b_min_score:
            return PortfolioTierLevel.TIER_B_STRONG_MATCH.value
        return PortfolioTierLevel.TIER_C_RESEARCH_PIPELINE.value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "global_target": self.global_target,
            "academic_target": self.academic_target,
            "startup_target": f"{self.startup_target_min}–{self.startup_target_max}",
            "other_target": f"{self.other_target_min}–{self.other_target_max}",
            "quality_thresholds": {
                "tier_a": self.tier_a_min_score,
                "tier_b": self.tier_b_min_score,
                "tier_c": self.tier_c_min_score
            }
        }


# Default system-wide configuration singleton
portfolio_config = PortfolioCapacityConfig()
