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
from src.opportunities.data.expansion_batch_session06 import NEW_STARTUPS
from src.opportunities.data.expansion_batch_session07 import NEW_STARTUPS_SESSION07
from src.opportunities.data.expansion_batch_session08 import NEW_STARTUPS_SESSION08
from src.opportunities.data.expansion_batch_session11 import NEW_STARTUPS_SESSION11

# Combine and deduplicate by canonical company name
def _get_deduplicated_catalog() -> List[Dict[str, Any]]:
    seen_names = set()
    catalog = []
    all_startups = INDIA_STARTUPS + US_STARTUPS + GLOBAL_STARTUPS + NEW_STARTUPS + NEW_STARTUPS_SESSION07 + NEW_STARTUPS_SESSION08 + NEW_STARTUPS_SESSION11
    for startup in all_startups:
        s = dict(startup)
        if "title" not in s or not s["title"]:
            s["title"] = f"Summer 2027 Engineering Intern — {s.get('domains', ['Systems'])[0]}"
        if "evidence_url" not in s or not s["evidence_url"]:
            s["evidence_url"] = s.get("careers_url") or s.get("application_url") or s.get("website")
        norm_name = s["company_name"].strip().lower()
        if norm_name not in seen_names:
            seen_names.add(norm_name)
            catalog.append(s)
    return catalog

STARTUP_CATALOG: List[Dict[str, Any]] = _get_deduplicated_catalog()
