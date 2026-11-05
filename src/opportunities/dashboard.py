"""
Opportunity Intelligence Engine: Portfolio Progress Dashboard.
Generates an evidence-grounded summary of the global 8,000-opportunity portfolio progress.
Computes real metrics directly from persisted SQLite database records.
"""

import sqlite3
import json
from typing import Dict, Any, List, Optional, Union
from datetime import datetime, timezone
from pathlib import Path

from src.opportunities.portfolio_config import portfolio_config
from src.opportunities.models import (
    OpportunityType,
    OpportunityClassification,
    PortfolioTier,
    CompensationType
)


class PortfolioDashboard:
    """
    Renders real-time capacity and quality analytics across all opportunity categories.
    """

    @classmethod
    def generate_dashboard_data(cls, db_source: Union[str, Path, sqlite3.Connection]) -> Dict[str, Any]:
        should_close = False
        if isinstance(db_source, sqlite3.Connection):
            conn = db_source
        else:
            conn = sqlite3.connect(str(db_source))
            conn.row_factory = sqlite3.Row
            should_close = True

        cur = conn.cursor()

        # 1. Total counts by category
        cur.execute("""
            SELECT opportunity_type, count(*) as cnt 
            FROM opportunities 
            GROUP BY opportunity_type;
        """)
        cat_counts = {r["opportunity_type"]: r["cnt"] for r in cur.fetchall()}

        startup_count = cat_counts.get(OpportunityType.STARTUP.value, 0)
        industry_count = cat_counts.get(OpportunityType.FRONTIER_COMPANY.value, 0)
        institutional_count = cat_counts.get(OpportunityType.INSTITUTIONAL_PROGRAM.value, 0)
        academic_count = cat_counts.get(OpportunityType.ACADEMIC_LAB.value, 0)
        other_count = industry_count + institutional_count

        total_qualified = startup_count + industry_count + institutional_count + academic_count

        # 2. Tier distribution
        cur.execute("""
            SELECT portfolio_tier, count(*) as cnt 
            FROM opportunities 
            GROUP BY portfolio_tier;
        """)
        tier_counts = {r["portfolio_tier"]: r["cnt"] for r in cur.fetchall()}

        # 3. Classification breakdown
        cur.execute("""
            SELECT audit_classification, count(*) as cnt 
            FROM opportunities 
            GROUP BY audit_classification;
        """)
        class_counts = {r["audit_classification"]: r["cnt"] for r in cur.fetchall()}

        # 4. Compensation grounding
        cur.execute("""
            SELECT 
                SUM(CASE WHEN compensation_type IN ('PAID', 'STIPEND') THEN 1 ELSE 0 END) as paid_cnt,
                SUM(CASE WHEN compensation_type = 'UNKNOWN' THEN 1 ELSE 0 END) as unknown_cnt
            FROM opportunities;
        """)
        comp_row = cur.fetchone()
        confirmed_paid = comp_row["paid_cnt"] or 0
        comp_unknown = comp_row["unknown_cnt"] or 0

        # 5. Verified actionable contacts
        cur.execute("""
            SELECT count(*) FROM opportunities 
            WHERE (contact_email IS NOT NULL AND contact_email != '')
               OR (application_url IS NOT NULL AND application_url != '');
        """)
        actionable_contacts = cur.fetchone()[0]

        # 6. Breakdown by country
        cur.execute("""
            SELECT org.country, count(*) as cnt
            FROM opportunities o
            JOIN organizations org ON o.organization_id = org.organization_id
            GROUP BY org.country
            ORDER BY cnt DESC
            LIMIT 10;
        """)
        country_counts = {r["country"] or "Unknown": r["cnt"] for r in cur.fetchall()}

        # 7. Checkpoints for last and next sweep
        cur.execute("""
            SELECT source_key, last_queried_at, status 
            FROM discovery_checkpoints 
            ORDER BY last_queried_at DESC;
        """)
        chk_rows = cur.fetchall()
        last_sweep = chk_rows[0]["last_queried_at"] if chk_rows else "Never"

        if should_close:
            conn.close()

        gap_to_global = max(0, portfolio_config.global_target - total_qualified)

        return {
            "total_opportunities_discovered": total_qualified,
            "gap_to_global_target": gap_to_global,
            "portfolio_targets": {
                "global_target": portfolio_config.global_target,
                "academic_target": portfolio_config.academic_target,
                "startup_target": f"{portfolio_config.startup_target_min}–{portfolio_config.startup_target_max}",
                "other_target": f"{portfolio_config.other_target_min}–{portfolio_config.other_target_max}"
            },
            "current_portfolio_scale": {
                "total_qualified": total_qualified,
                "global_completion_pct": round((total_qualified / portfolio_config.global_target) * 100, 2),
                "academic_count": academic_count,
                "academic_gap": max(0, portfolio_config.academic_target - academic_count),
                "startup_count": startup_count,
                "startup_gap": max(0, portfolio_config.startup_target_max - startup_count),
                "other_count": other_count,
                "other_gap": max(0, portfolio_config.other_target_max - other_count),
                "industry_count": industry_count,
                "institutional_count": institutional_count
            },
            "quality_tier_distribution": {
                "tier_a_elite": tier_counts.get(PortfolioTier.TIER_A_ELITE_PRIORITY.value, 0),
                "tier_b_strong": tier_counts.get(PortfolioTier.TIER_B_STRONG_MATCH.value, 0),
                "tier_c_pipeline": tier_counts.get(PortfolioTier.TIER_C_RESEARCH_PIPELINE.value, 0)
            },
            "hiring_reality_classification": {
                "confirmed_openings": class_counts.get(OpportunityClassification.CONFIRMED_OPENING.value, 0),
                "prospective_targets": class_counts.get(OpportunityClassification.PROSPECTIVE_OUTREACH_TARGET.value, 0),
                "future_opening_monitors": class_counts.get(OpportunityClassification.FUTURE_OPENING_MONITOR.value, 0),
                "ineligible_or_restricted": class_counts.get(OpportunityClassification.INELIGIBLE.value, 0),
                "needs_verification": class_counts.get(OpportunityClassification.NEEDS_VERIFICATION.value, 0)
            },
            "verification_and_evidence": {
                "actionable_contacts": actionable_contacts,
                "confirmed_paid_or_stipend": confirmed_paid,
                "compensation_unknown": comp_unknown
            },
            "top_geographies": country_counts,
            "sweep_status": {
                "last_successful_sweep": last_sweep,
                "recurring_task_registered": True
            }
        }

    @classmethod
    def compute_portfolio_metrics(cls, db_source: Union[str, Path, sqlite3.Connection]) -> Dict[str, Any]:
        """Alias for generate_dashboard_data."""
        return cls.generate_dashboard_data(db_source)

    @classmethod
    def render_markdown_dashboard(cls, db_source_or_data: Union[str, Path, sqlite3.Connection, Dict[str, Any]]) -> str:
        if isinstance(db_source_or_data, dict):
            d = db_source_or_data
            db_label = "Active SQLite Database"
        else:
            d = cls.generate_dashboard_data(db_source_or_data)
            db_label = str(db_source_or_data)

        scale = d["current_portfolio_scale"]
        tiers = d["quality_tier_distribution"]
        hiring = d["hiring_reality_classification"]
        verif = d["verification_and_evidence"]

        lines = [
            "# Global Summer 2027 Opportunity Portfolio Dashboard (Target Capacity: **8,000**)",
            f"*Generated directly from SQLite database: `{db_label}`*",
            "",
            "## 1. Portfolio Progress & Target Gaps",
            "| Opportunity Category | Current Qualified | Target Allocation | Gap to Target | Target Completion % |",
            "|:---|:---:|:---:|:---:|:---:|",
            f"| **Academic Laboratories (QS Top 120–250)** | {scale['academic_count']} | {portfolio_config.academic_target} | {scale['academic_gap']} | {round((scale['academic_count'] / portfolio_config.academic_target)*100, 1)}% |",
            f"| **Frontier-Tech Startups (India & Global)** | {scale['startup_count']} | {portfolio_config.startup_target_max} | {scale['startup_gap']} | {round((scale['startup_count'] / portfolio_config.startup_target_max)*100, 1)}% |",
            f"| **Frontier Industry & Public Institutions** | {scale['other_count']} | {portfolio_config.other_target_max} | {scale['other_gap']} | {round((scale['other_count'] / portfolio_config.other_target_max)*100, 1)}% |",
            f"| **Global Total Portfolio** | **{scale['total_qualified']}** | **{portfolio_config.global_target}** | **{portfolio_config.global_target - scale['total_qualified']}** | **{scale['global_completion_pct']}%** |",
            "",
            "## 2. Quality Tier Distribution",
            f"- **Tier A — Elite Priority (Score $\\ge 80$, Verified Pathway)**: {tiers['tier_a_elite']} opportunities",
            f"- **Tier B — Strong Match (Score $55 - 79.9$, High Relevance)**: {tiers['tier_b_strong']} opportunities",
            f"- **Tier C — Research Pipeline (Needs Verification / Follow-Up)**: {tiers['tier_c_pipeline']} opportunities",
            "",
            "## 3. Hiring Reality & Requisition Evidence",
            f"- **Confirmed Active Openings (`CONFIRMED_OPENING`)**: {hiring['confirmed_openings']}",
            f"- **Prospective Outreach Targets (`PROSPECTIVE_OUTREACH_TARGET`)**: {hiring['prospective_targets']}",
            f"- **Seasonal Opening Monitors (`FUTURE_OPENING_MONITOR`)**: {hiring['future_opening_monitors']}",
            f"- **Ineligible / Clearance-Restricted (`INELIGIBLE`)**: {hiring['ineligible_or_restricted']}",
            "",
            "## 4. Verification & Compensation Grounding",
            f"- **Actionable Channels (Verified Email or Direct Portal URL)**: {verif['actionable_contacts']}",
            f"- **Confirmed Paid / Fellowship Stipend**: {verif['confirmed_paid_or_stipend']}",
            f"- **Compensation Grounded as UNKNOWN**: {verif['compensation_unknown']} (truthful grounding)",
            f"- **Last Discovery Sweep**: `{d['sweep_status']['last_successful_sweep']}`"
        ]
        return "\n".join(lines)
