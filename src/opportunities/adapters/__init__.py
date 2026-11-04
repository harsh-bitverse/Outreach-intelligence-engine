"""
Opportunity Intelligence Engine: Adapters Package.
"""

from src.opportunities.adapters.base import BaseOpportunityAdapter
from src.opportunities.adapters.academic_lab import AcademicLabAdapter
from src.opportunities.adapters.startup import StartupAdapter
from src.opportunities.adapters.frontier_industry import FrontierIndustryAdapter
from src.opportunities.adapters.institutional_program import InstitutionalProgramAdapter

__all__ = [
    "BaseOpportunityAdapter",
    "AcademicLabAdapter",
    "StartupAdapter",
    "FrontierIndustryAdapter",
    "InstitutionalProgramAdapter",
]
