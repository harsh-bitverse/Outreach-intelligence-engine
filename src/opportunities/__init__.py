"""
Opportunity Intelligence Engine: Continuous Global Discovery and Qualification System.
"""

from src.opportunities.models import (
    Organization,
    Opportunity,
    OpportunityType,
    CompensationType,
    ContactChannel,
    VerificationStatus,
    DiscoveryStatus,
    PriorityTier,
    ApplicationStatus,
    QualificationBreakdown,
    DiscoveryCheckpoint,
    DiscoveryRun
)
from src.opportunities.schema import apply_opportunity_migrations
from src.opportunities.repository import OpportunityRepository
from src.opportunities.registry import (
    SourceRegistry,
    canonicalize_url,
    normalize_organization_name,
    compute_organization_id,
    compute_opportunity_id
)
from src.opportunities.ranking import OpportunityRanker
from src.opportunities.workbook_sync import OpportunityWorkbookSync
from src.opportunities.discovery_worker import OpportunityDiscoveryWorker
from src.opportunities.scheduling import OpportunityScheduler
from src.opportunities.personalization import DomainAdaptivePersonalizer
from src.opportunities.budget_and_tracking import GlobalBudgetManager, UnifiedDeliveryBridge

__all__ = [
    "Organization",
    "Opportunity",
    "OpportunityType",
    "CompensationType",
    "ContactChannel",
    "VerificationStatus",
    "DiscoveryStatus",
    "PriorityTier",
    "ApplicationStatus",
    "QualificationBreakdown",
    "DiscoveryCheckpoint",
    "DiscoveryRun",
    "apply_opportunity_migrations",
    "OpportunityRepository",
    "SourceRegistry",
    "canonicalize_url",
    "normalize_organization_name",
    "compute_organization_id",
    "compute_opportunity_id",
    "OpportunityRanker",
    "OpportunityWorkbookSync",
    "OpportunityDiscoveryWorker",
    "OpportunityScheduler",
    "DomainAdaptivePersonalizer",
    "GlobalBudgetManager",
    "UnifiedDeliveryBridge"
]
