"""
Opportunity Intelligence Engine: Abstract Base Source Adapter.
Defines the standard discovery, normalization, verification, qualification, and execution lifecycle.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

from src.opportunities.models import (
    Organization,
    Opportunity,
    DiscoveryCheckpoint,
    DiscoveryRun,
    DiscoveryStatus
)
from src.opportunities.ranking import OpportunityRanker
from src.opportunities.registry import (
    SourceRegistry,
    canonicalize_url,
    compute_organization_id,
    compute_opportunity_id
)
from src.opportunities.repository import OpportunityRepository
from src.security.audit_logger import audit_logger


class BaseOpportunityAdapter(ABC):
    """
    Standard interface for all category discovery adapters.
    Ensures consistent scoring, grounding, deduplication, and persistence.
    """

    def __init__(self, adapter_name: str, category: str):
        self.adapter_name = adapter_name
        self.category = category

    @abstractmethod
    def fetch_raw_opportunities(
        self,
        checkpoint: Optional[DiscoveryCheckpoint] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Fetch candidate opportunities from registered sources or Antigravity CLI research prompts.
        Must return list of raw dicts containing official source evidence.
        """
        pass

    @abstractmethod
    def normalize_raw_item(self, raw_item: Dict[str, Any]) -> Tuple[Organization, Opportunity]:
        """
        Convert raw scraped/discovered dictionary into type-safe Organization and Opportunity instances.
        """
        pass

    def verify_grounding(self, opp: Opportunity) -> bool:
        """
        Strict ground-truth validation:
        1. Must contain at least one official evidence URL.
        2. Must be hosted on a trustworthy domain.
        3. Never invent jobs, salaries, or deadlines.
        """
        if not opp.official_evidence_urls:
            return False

        for url in opp.official_evidence_urls:
            if SourceRegistry.is_trusted_url(url):
                return True
        return False

    def qualify_and_score(self, opp: Opportunity) -> Opportunity:
        """
        Applies the multi-factor qualification engine.
        Updates qualification_score, score_breakdown, discovery_status, and matched_project_id.
        """
        score, breakdown, discovery_status, matched_project = OpportunityRanker.evaluate_opportunity(opp)
        opp.qualification_score = score
        opp.score_breakdown = breakdown
        opp.discovery_status = discovery_status
        if matched_project:
            opp.matched_project_id = matched_project
        return opp

    def run_discovery_cycle(
        self,
        repo: OpportunityRepository,
        limit: int = 50
    ) -> DiscoveryRun:
        """
        Executes an end-to-end atomic discovery cycle:
        1. Retrieves durable checkpoint.
        2. Fetches raw candidates.
        3. Normalizes into Organization and Opportunity.
        4. Verifies grounding against official sources.
        5. Deduplicates against existing database records.
        6. Scores and qualifies candidates.
        7. Persists qualified candidates and updates checkpoint.
        """
        run_id = f"run_{self.adapter_name}_{int(datetime.now(timezone.utc).timestamp())}"
        run = DiscoveryRun(run_id=run_id, adapter_name=self.adapter_name)
        checkpoint = repo.get_checkpoint(self.adapter_name) or DiscoveryCheckpoint(
            source_key=self.adapter_name,
            category=self.category
        )

        try:
            raw_items = self.fetch_raw_opportunities(checkpoint=checkpoint, limit=limit)
            run.opportunities_found = len(raw_items)

            for raw in raw_items:
                try:
                    org, opp = self.normalize_raw_item(raw)

                    # 1. Grounding verification
                    if not self.verify_grounding(opp):
                        run.errors.append(f"Grounding failed for {opp.title} at {org.name}: missing official URL")
                        continue

                    # 2. Deduplication check
                    if repo.is_opportunity_duplicate(opp.opportunity_id, opp.application_url, opp.contact_email):
                        continue

                    # 3. Qualification & Scoring
                    self.qualify_and_score(opp)

                    # 4. Save organization & opportunity
                    repo.save_organization(org)
                    repo.save_opportunity(opp)

                    if opp.discovery_status in (DiscoveryStatus.QUALIFIED.value, DiscoveryStatus.NEEDS_REVIEW.value):
                        run.opportunities_qualified += 1

                except Exception as item_err:
                    run.errors.append(f"Error processing item: {str(item_err)}")

            # Update checkpoint
            checkpoint.items_discovered_count += run.opportunities_qualified
            checkpoint.status = "SUCCESS"
            checkpoint.last_queried_at = datetime.now(timezone.utc).isoformat()
            repo.save_checkpoint(checkpoint)

            run.status = "COMPLETED"
            run.completed_at = datetime.now(timezone.utc).isoformat()
            repo.save_discovery_run(run)

            audit_logger.log_event(
                event_type="OPPORTUNITY_DISCOVERY_CYCLE_COMPLETED",
                actor=self.adapter_name,
                entity_type="DISCOVERY_RUN",
                entity_id=run_id,
                details={
                    "found": run.opportunities_found,
                    "qualified": run.opportunities_qualified,
                    "errors": len(run.errors)
                }
            )
            return run

        except Exception as e:
            run.status = "FAILED"
            run.errors.append(str(e))
            run.completed_at = datetime.now(timezone.utc).isoformat()
            repo.save_discovery_run(run)

            checkpoint.status = "ERROR"
            checkpoint.error_message = str(e)
            repo.save_checkpoint(checkpoint)
            raise
