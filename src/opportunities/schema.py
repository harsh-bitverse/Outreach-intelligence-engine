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
    priority_tier TEXT DEFAULT 'NEEDS_VERIFICATION',
    portfolio_tier TEXT DEFAULT 'TIER_B_STRONG_MATCH',
    audit_classification TEXT DEFAULT 'NEEDS_VERIFICATION',
    requisition_type TEXT DEFAULT 'PROSPECTIVE_PITCH_TARGET',
    role_title_type TEXT DEFAULT 'PROPOSED_OUTREACH_FOCUS',
    workplace_type TEXT DEFAULT 'UNKNOWN',
    international_eligibility TEXT DEFAULT 'NEEDS_CONFIRMATION',
    compensation_evidence TEXT DEFAULT 'UNPUBLISHED_BY_EMPLOYER',
    stipend_amount TEXT,
    travel_support TEXT,
    housing_support TEXT,
    required_documents TEXT,
    -- Academic qualification, feasibility & reply integration dimensions
    academic_classification TEXT,
    institution_visiting_policy TEXT DEFAULT 'POLICY_UNVERIFIED',
    hosting_history_verified INTEGER DEFAULT 0,
    hosting_history_evidence TEXT,
    summer_2027_hosting_availability TEXT DEFAULT 'UNKNOWN',
    funding_availability TEXT DEFAULT 'UNKNOWN',
    visa_sponsorship_status TEXT DEFAULT 'UNKNOWN',
    term_compatibility INTEGER DEFAULT 1,
    application_route TEXT DEFAULT 'DIRECT_FACULTY_PI',
    evidence_sources TEXT,
    evidence_timestamp TEXT,
    verification_confidence TEXT DEFAULT 'MEDIUM',
    academic_reply_outcome TEXT,
    reply_timestamp TEXT,
    reply_evidence_snippet TEXT,
    reply_evidence_id TEXT,
    professor_id TEXT,
    operational_status TEXT DEFAULT 'OPERATIONAL',
    verification_notes TEXT,
    last_verified_at TEXT,
    contact_history_status TEXT DEFAULT 'DISCOVERED',
    assigned_block_id TEXT,
    recipient_mailing_window TEXT,
    ist_eligibility_window TEXT,
    discovery_status TEXT DEFAULT 'DISCOVERED',
    application_status TEXT DEFAULT 'NOT_APPLIED',
    is_confirmed_opening INTEGER DEFAULT 1,
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

    # Ensure backward-compatible column migration
    if "opportunities" in tables:
        cursor.execute("PRAGMA table_info(opportunities);")
        columns = [row[1] for row in cursor.fetchall()]
        if "priority_tier" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN priority_tier TEXT DEFAULT 'NEEDS_VERIFICATION';")
        if "is_confirmed_opening" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN is_confirmed_opening INTEGER DEFAULT 1;")
        if "audit_classification" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN audit_classification TEXT DEFAULT 'NEEDS_VERIFICATION';")
        if "role_title_type" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN role_title_type TEXT DEFAULT 'PROPOSED_OUTREACH_FOCUS';")
        if "workplace_type" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN workplace_type TEXT DEFAULT 'UNKNOWN';")
        if "international_eligibility" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN international_eligibility TEXT DEFAULT 'NEEDS_CONFIRMATION';")
        if "compensation_evidence" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN compensation_evidence TEXT DEFAULT 'UNPUBLISHED_BY_EMPLOYER';")
        if "operational_status" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN operational_status TEXT DEFAULT 'OPERATIONAL';")
        if "portfolio_tier" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN portfolio_tier TEXT DEFAULT 'TIER_B_STRONG_MATCH';")
        if "requisition_type" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN requisition_type TEXT DEFAULT 'PROSPECTIVE_PITCH_TARGET';")
        if "stipend_amount" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN stipend_amount TEXT;")
        if "travel_support" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN travel_support TEXT;")
        if "housing_support" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN housing_support TEXT;")
        if "required_documents" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN required_documents TEXT;")
        if "verification_notes" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN verification_notes TEXT;")
        if "last_verified_at" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN last_verified_at TEXT;")
        if "academic_classification" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN academic_classification TEXT;")
        if "institution_visiting_policy" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN institution_visiting_policy TEXT DEFAULT 'POLICY_UNVERIFIED';")
        if "hosting_history_verified" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN hosting_history_verified INTEGER DEFAULT 0;")
        if "hosting_history_evidence" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN hosting_history_evidence TEXT;")
        if "summer_2027_hosting_availability" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN summer_2027_hosting_availability TEXT DEFAULT 'UNKNOWN';")
        if "funding_availability" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN funding_availability TEXT DEFAULT 'UNKNOWN';")
        if "visa_sponsorship_status" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN visa_sponsorship_status TEXT DEFAULT 'UNKNOWN';")
        if "term_compatibility" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN term_compatibility INTEGER DEFAULT 1;")
        if "application_route" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN application_route TEXT DEFAULT 'DIRECT_FACULTY_PI';")
        if "evidence_sources" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN evidence_sources TEXT;")
        if "evidence_timestamp" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN evidence_timestamp TEXT;")
        if "verification_confidence" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN verification_confidence TEXT DEFAULT 'MEDIUM';")
        if "academic_reply_outcome" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN academic_reply_outcome TEXT;")
        if "reply_timestamp" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN reply_timestamp TEXT;")
        if "reply_evidence_snippet" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN reply_evidence_snippet TEXT;")
        if "reply_evidence_id" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN reply_evidence_id TEXT;")
        if "professor_id" not in columns:
            cursor.execute("ALTER TABLE opportunities ADD COLUMN professor_id TEXT;")
        conn.commit()

    logger.info(f"Opportunity schema migration applied successfully. Tables verified: {tables}")
    return {
        "status": "MIGRATIONS_APPLIED",
        "verified_tables": tables,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
