"""
Opportunity Intelligence Engine: Schema Definitions and Migrations.

Defines the normalized database schema for multi-category opportunity discovery:
- Academic Research Laboratories
- Technical Startups (Indian and International)
- Frontier-Tech Companies & Industrial R&D
- Publicly Funded Research Laboratories & National/International Programs

Maintains strict non-destructive isolation from legacy tables.
"""

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

OPPORTUNITY_SCHEMA_SQL = """
-- 1. Organizations (Companies, Labs, Universities, Research Institutes)
CREATE TABLE IF NOT EXISTS organizations (
    organization_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    website_url TEXT,
    country TEXT,
    city TEXT,
    iana_timezone TEXT DEFAULT 'UTC',
    description TEXT,
    funding_stage TEXT,
    tier TEXT DEFAULT 'STANDARD',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_organizations_category ON organizations(category);
CREATE INDEX IF NOT EXISTS idx_organizations_name ON organizations(name);

-- 2. Normalized Opportunities
CREATE TABLE IF NOT EXISTS opportunities (
    opportunity_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    title TEXT NOT NULL,
    opportunity_type TEXT NOT NULL,
    source TEXT NOT NULL,
    location TEXT,
    iana_timezone TEXT DEFAULT 'UTC',
    technical_domains TEXT, -- JSON array of strings
    technical_relevance_summary TEXT,
    potential_contribution TEXT,
    matched_project_id TEXT,
    eligibility_criteria TEXT,
    internship_duration_weeks INTEGER DEFAULT 10,
    target_term TEXT DEFAULT 'Summer 2027',
    compensation_type TEXT DEFAULT 'UNKNOWN',
    compensation_details TEXT,
    official_evidence_urls TEXT, -- JSON array of URLs
    freshness_timestamp TEXT,
    hiring_contact_channel TEXT,
    contact_email TEXT,
    contact_name TEXT,
    contact_verification_status TEXT DEFAULT 'UNVERIFIED',
    application_url TEXT,
    application_deadline TEXT,
    qualification_score REAL DEFAULT 0.0,
    score_breakdown TEXT, -- JSON dict of category scores
    discovery_status TEXT DEFAULT 'DISCOVERED',
    application_status TEXT DEFAULT 'NOT_APPLIED',
    next_action TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_checked_at TEXT,
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id)
);

CREATE INDEX IF NOT EXISTS idx_opportunities_org_id ON opportunities(organization_id);
CREATE INDEX IF NOT EXISTS idx_opportunities_type ON opportunities(opportunity_type);
CREATE INDEX IF NOT EXISTS idx_opportunities_score ON opportunities(qualification_score DESC);
CREATE INDEX IF NOT EXISTS idx_opportunities_discovery_status ON opportunities(discovery_status);
CREATE INDEX IF NOT EXISTS idx_opportunities_application_status ON opportunities(application_status);
CREATE INDEX IF NOT EXISTS idx_opportunities_contact_email ON opportunities(contact_email);

-- 3. Discovery Checkpoints
CREATE TABLE IF NOT EXISTS discovery_checkpoints (
    source_key TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    last_queried_at TEXT NOT NULL,
    cursor_or_token TEXT,
    items_discovered_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'IDLE',
    error_message TEXT,
    updated_at TEXT NOT NULL
);

-- 4. Discovery Execution Runs
CREATE TABLE IF NOT EXISTS discovery_runs (
    run_id TEXT PRIMARY KEY,
    adapter_name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    opportunities_found INTEGER DEFAULT 0,
    opportunities_qualified INTEGER DEFAULT 0,
    errors TEXT, -- JSON list of errors
    status TEXT NOT NULL
);
"""


def apply_opportunity_migrations(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Apply Opportunity Intelligence Engine schema migrations idempotently.
    Safe for existing databases: does not touch or drop any pre-existing tables.
    """
    cursor = conn.cursor()
    cursor.executescript(OPPORTUNITY_SCHEMA_SQL)
    conn.commit()

    # Verify tables created
    cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name IN ('organizations', 'opportunities', 'discovery_checkpoints', 'discovery_runs');
    """)
    tables = [row[0] for row in cursor.fetchall()]

    logger.info(f"Opportunity schema migration applied successfully. Tables verified: {tables}")
    return {
        "status": "MIGRATIONS_APPLIED",
        "verified_tables": tables,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
