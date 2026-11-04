"""
Opportunity Intelligence Engine: Unified Regional Time-Block & Deadline Scheduler.

Implements unified regional time-block distribution adhering to the 13 IST regional time blocks:
- BLOCK_01_JP_KR: Japan & South Korea (04:30 - 05:30 IST)
- BLOCK_02_SG_CN_HK_TW: Singapore, China, Hong Kong, Taiwan (05:30 - 06:30 IST)
- BLOCK_03_SEA: Southeast Asia (06:30 - 07:30 IST)
- BLOCK_DOMESTIC_IN: India Domestic (08:00 - 09:30 IST) [New Regional Block]
- BLOCK_04_GULF: UAE & Gulf (09:30 - 10:30 IST)
- BLOCK_05_SA_TR_RU: Saudi Arabia, Turkey, Russia (10:30 - 11:30 IST)
- BLOCK_06_EAST_EU: Eastern Europe (11:30 - 12:30 IST)
- BLOCK_07_CONT_EU: Continental Europe (12:30 - 13:30 IST)
- BLOCK_08_UK_IE_PT: United Kingdom, Ireland, Portugal (13:30 - 14:30 IST)
- BLOCK_09_US_EAST: US Eastern & Canada Eastern (18:30 - 19:30 IST winter / 17:30 - 18:30 IST summer)
- BLOCK_10_US_CENTRAL: US Central & Canada Central (19:30 - 20:30 IST winter / 18:30 - 19:30 IST summer)
- BLOCK_11_US_MOUNTAIN: US Mountain & Canada Mountain (20:30 - 21:30 IST winter / 19:30 - 20:30 IST summer)
- BLOCK_12_US_PACIFIC: US Pacific & Canada Pacific (21:30 - 22:30 IST winter / 20:30 - 21:30 IST summer)
- BLOCK_13_ANZ: Australia & New Zealand (02:30 - 03:30 IST)

Separates:
1. Email-addressed opportunities -> Regional IST working-hour dispatch blocks.
2. Portal-addressed opportunities -> Deadline urgency schedules (Deadlines, Required Materials).
"""

from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from zoneinfo import ZoneInfo

from src.opportunities.models import Opportunity, Organization, ContactChannel, OpportunityType


class OpportunityScheduler:
    """
    Unified time-block engine for multi-category opportunities.
    """

    TIMEZONE_TO_BLOCK = {
        # India Domestic
        "Asia/Kolkata": {
            "block_id": "BLOCK_DOMESTIC_IN",
            "region": "India Domestic",
            "local_target": "8:00 AM - 9:00 AM IST",
            "ist_window": "08:00 - 09:30 IST"
        },
        # Japan & Korea
        "Asia/Tokyo": {
            "block_id": "BLOCK_01_JP_KR",
            "region": "Japan & South Korea",
            "local_target": "8:00 AM JST (UTC+9)",
            "ist_window": "04:30 - 05:30 IST"
        },
        "Asia/Seoul": {
            "block_id": "BLOCK_01_JP_KR",
            "region": "Japan & South Korea",
            "local_target": "8:00 AM KST (UTC+9)",
            "ist_window": "04:30 - 05:30 IST"
        },
        # Singapore & East Asia
        "Asia/Singapore": {
            "block_id": "BLOCK_02_SG_CN_HK_TW",
            "region": "Singapore & East Asia",
            "local_target": "8:00 AM SGT (UTC+8)",
            "ist_window": "05:30 - 06:30 IST"
        },
        "Asia/Hong_Kong": {
            "block_id": "BLOCK_02_SG_CN_HK_TW",
            "region": "Singapore & East Asia",
            "local_target": "8:00 AM HKT (UTC+8)",
            "ist_window": "05:30 - 06:30 IST"
        },
        # Continental Europe
        "Europe/Berlin": {
            "block_id": "BLOCK_07_CONT_EU",
            "region": "Continental Europe",
            "local_target": "8:00 AM CET/CEST (UTC+1/+2)",
            "ist_window": "12:30 - 13:30 IST"
        },
        "Europe/Paris": {
            "block_id": "BLOCK_07_CONT_EU",
            "region": "Continental Europe",
            "local_target": "8:00 AM CET/CEST (UTC+1/+2)",
            "ist_window": "12:30 - 13:30 IST"
        },
        "Europe/Zurich": {
            "block_id": "BLOCK_07_CONT_EU",
            "region": "Continental Europe",
            "local_target": "8:00 AM CET/CEST (UTC+1/+2)",
            "ist_window": "12:30 - 13:30 IST"
        },
        "Europe/Amsterdam": {
            "block_id": "BLOCK_07_CONT_EU",
            "region": "Continental Europe",
            "local_target": "8:00 AM CET/CEST (UTC+1/+2)",
            "ist_window": "12:30 - 13:30 IST"
        },
        "Europe/Oslo": {
            "block_id": "BLOCK_07_CONT_EU",
            "region": "Continental Europe",
            "local_target": "8:00 AM CET/CEST (UTC+1/+2)",
            "ist_window": "12:30 - 13:30 IST"
        },
        "Europe/Vilnius": {
            "block_id": "BLOCK_06_EAST_EU",
            "region": "Eastern Europe",
            "local_target": "8:00 AM EET (UTC+2)",
            "ist_window": "11:30 - 12:30 IST"
        },
        # UK & Ireland
        "Europe/London": {
            "block_id": "BLOCK_08_UK_IE_PT",
            "region": "UK, Ireland & Portugal",
            "local_target": "8:00 AM GMT/BST (UTC+0/+1)",
            "ist_window": "13:30 - 14:30 IST"
        },
        # US Eastern
        "America/New_York": {
            "block_id": "BLOCK_09_US_EAST",
            "region": "US Eastern & Canada Eastern",
            "local_target": "8:00 AM EST/EDT (UTC-5/-4)",
            "ist_window": "18:30 - 19:30 IST"
        },
        "America/Toronto": {
            "block_id": "BLOCK_09_US_EAST",
            "region": "US Eastern & Canada Eastern",
            "local_target": "8:00 AM EST/EDT (UTC-5/-4)",
            "ist_window": "18:30 - 19:30 IST"
        },
        # US Central
        "America/Chicago": {
            "block_id": "BLOCK_10_US_CENTRAL",
            "region": "US Central & Canada Central",
            "local_target": "8:00 AM CST/CDT (UTC-6/-5)",
            "ist_window": "19:30 - 20:30 IST"
        },
        # US Pacific
        "America/Los_Angeles": {
            "block_id": "BLOCK_12_US_PACIFIC",
            "region": "US Pacific & Canada Pacific",
            "local_target": "8:00 AM PST/PDT (UTC-8/-7)",
            "ist_window": "21:30 - 22:30 IST"
        },
        # Australia
        "Australia/Sydney": {
            "block_id": "BLOCK_13_ANZ",
            "region": "Australia & New Zealand",
            "local_target": "8:00 AM AEST/AEDT (UTC+10/+11)",
            "ist_window": "02:30 - 03:30 IST"
        },
        "Australia/Brisbane": {
            "block_id": "BLOCK_13_ANZ",
            "region": "Australia & New Zealand",
            "local_target": "8:00 AM AEST (UTC+10)",
            "ist_window": "03:30 - 04:30 IST"
        }
    }

    @classmethod
    def resolve_time_block(cls, iana_timezone: str, country: Optional[str] = None) -> Dict[str, Any]:
        """Maps an IANA timezone or country to its regional dispatch block."""
        if iana_timezone in cls.TIMEZONE_TO_BLOCK:
            return cls.TIMEZONE_TO_BLOCK[iana_timezone]

        # Country fallbacks
        c_upper = (country or "").upper()
        if "INDIA" in c_upper:
            return cls.TIMEZONE_TO_BLOCK["Asia/Kolkata"]
        elif "UNITED KINGDOM" in c_upper or "UK" in c_upper:
            return cls.TIMEZONE_TO_BLOCK["Europe/London"]
        elif "GERMANY" in c_upper or "FRANCE" in c_upper or "SWITZERLAND" in c_upper:
            return cls.TIMEZONE_TO_BLOCK["Europe/Berlin"]
        elif "JAPAN" in c_upper:
            return cls.TIMEZONE_TO_BLOCK["Asia/Tokyo"]
        elif "AUSTRALIA" in c_upper:
            return cls.TIMEZONE_TO_BLOCK["Australia/Sydney"]
        elif "UNITED STATES" in c_upper or "USA" in c_upper:
            return cls.TIMEZONE_TO_BLOCK["America/Los_Angeles"]

        # Default fallback to US Pacific
        return {
            "block_id": "BLOCK_12_US_PACIFIC",
            "region": "US Pacific (Default)",
            "local_target": "8:00 AM PST (UTC-8)",
            "ist_window": "21:30 - 22:30 IST"
        }

    @classmethod
    def generate_unified_schedule(
        cls,
        opportunities: List[Opportunity],
        orgs_by_id: Dict[str, Organization]
    ) -> List[Dict[str, Any]]:
        """
        Generates development-only Unified Outreach Schedule records.
        - Email outreach contacts are assigned to regional time blocks and ordered deterministically.
        - Application portal opportunities are tracked with deadline urgency.
        """
        schedule_records = []

        # Sort all opportunities by score descending
        sorted_opps = sorted(opportunities, key=lambda o: (o.qualification_score, o.opportunity_id), reverse=True)

        for opp in sorted_opps:
            org = orgs_by_id.get(opp.organization_id)
            org_name = org.name if org else opp.organization_id
            block_info = cls.resolve_time_block(opp.iana_timezone, org.country if org else None)

            is_email = (opp.hiring_contact_channel == ContactChannel.EMAIL.value and bool(opp.contact_email))

            record = {
                "schedule_id": f"sched_{opp.opportunity_id[:10]}",
                "opportunity_id": opp.opportunity_id,
                "category": opp.opportunity_type,
                "organization_name": org_name,
                "role_title": opp.title,
                "channel": "EMAIL_DISPATCH" if is_email else "PORTAL_APPLICATION",
                "contact_recipient": opp.contact_email if is_email else (opp.application_url or "Portal"),
                "contact_name": opp.contact_name or "Hiring Team",
                "iana_timezone": opp.iana_timezone,
                "assigned_block_id": block_info["block_id"] if is_email else "PORTAL_DEADLINE_TRACKER",
                "region_name": block_info["region"] if is_email else "Official Careers Portal",
                "ist_window": block_info["ist_window"] if is_email else "N/A (Portal Submission)",
                "qualification_score": opp.qualification_score,
                "priority_tier": opp.priority_tier or "PROMISING",
                "deadline": opp.application_deadline or "Rolling",
                "status": "QUEUED_FOR_REVIEW" if is_email else "PORTAL_TRACKED",
                "next_action": opp.next_action
            }
            schedule_records.append(record)

        return schedule_records
