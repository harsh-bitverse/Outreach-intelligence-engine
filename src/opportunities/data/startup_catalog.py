"""
Opportunity Intelligence Engine: Unified Global Frontier-Tech Startup Catalog.

Aggregates 110+ real, verified startups across all 7 priority domains and geographies:
- India (28 startups)
- United States (52 startups)
- Europe, UK, Canada & Asia-Pacific (36 startups)
"""

from typing import List, Dict, Any
from src.opportunities.data.india_startups import INDIA_STARTUPS
from src.opportunities.data.us_startups import US_STARTUPS
from src.opportunities.data.global_startups import GLOBAL_STARTUPS

# Combine and deduplicate by canonical company name
def _get_deduplicated_catalog() -> List[Dict[str, Any]]:
    seen_names = set()
    catalog = []
    for startup in INDIA_STARTUPS + US_STARTUPS + GLOBAL_STARTUPS:
        norm_name = startup["company_name"].strip().lower()
        if norm_name not in seen_names:
            seen_names.add(norm_name)
            catalog.append(startup)
    return catalog

STARTUP_CATALOG: List[Dict[str, Any]] = _get_deduplicated_catalog()
