"""
Database Connection and Schema Initialization Module.
Provides SQLite connection management with WAL mode, foreign keys, and schema creation.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator
from config.settings import settings


SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Professors Table
CREATE TABLE IF NOT EXISTS professors (
    professor_id TEXT PRIMARY KEY,
    qs_rank INTEGER,
    processing_sequence INTEGER,
    university_name TEXT NOT NULL,
    country TEXT NOT NULL,
    full_name TEXT NOT NULL,
    title TEXT,
    department TEXT,
    email TEXT,
    email_verification_status TEXT DEFAULT 'UNVERIFIED',
    verification_source_url TEXT,
    faculty_profile_url TEXT,
    research_areas TEXT,
    research_overlap TEXT,
    cv_skill_project TEXT,
    potential_contribution TEXT,
    evidence_url TEXT,
    date_verified TEXT,
    notes TEXT,
    outreach_status TEXT DEFAULT 'NEW',
    time_block TEXT,
    time_block_order INTEGER,
    iana_timezone TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_professors_email ON professors(email);
CREATE INDEX IF NOT EXISTS idx_professors_university ON professors(university_name);
CREATE INDEX IF NOT EXISTS idx_professors_status ON professors(outreach_status);
CREATE INDEX IF NOT EXISTS idx_professors_qs ON professors(qs_rank);
CREATE INDEX IF NOT EXISTS idx_professors_time_block ON professors(time_block);
CREATE INDEX IF NOT EXISTS idx_professors_time_block_order ON professors(time_block_order);

-- Universities Table
CREATE TABLE IF NOT EXISTS universities (
    university_name TEXT PRIMARY KEY,
    country TEXT NOT NULL,
    qs_rank INTEGER,
    processing_sequence INTEGER,
    city TEXT,
    official_url TEXT,
    review_status TEXT DEFAULT 'UNREVIEWED',
    departments_reviewed TEXT,
    directories_reviewed TEXT,
    profiles_screened INTEGER DEFAULT 0,
    professors_shortlisted INTEGER DEFAULT 0,
    verified_emails INTEGER DEFAULT 0,
    unverified_emails INTEGER DEFAULT 0,
    timezone_iana TEXT DEFAULT 'UTC',
    last_updated TEXT NOT NULL
);

-- First-Contact Outreach Drafts Table
CREATE TABLE IF NOT EXISTS outreach_drafts (
    draft_id TEXT PRIMARY KEY,
    professor_id TEXT NOT NULL REFERENCES professors(professor_id) ON DELETE CASCADE,
    recipient_email TEXT NOT NULL,
    subject TEXT NOT NULL,
    body_text TEXT NOT NULL,
    word_count INTEGER NOT NULL,
    status TEXT DEFAULT 'DRAFT', -- DRAFT, APPROVED, REJECTED, QUEUED, SENT
    created_at TEXT NOT NULL,
    approved_at TEXT,
    approved_by TEXT,
    dispatched_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_outreach_drafts_prof ON outreach_drafts(professor_id);
CREATE INDEX IF NOT EXISTS idx_outreach_drafts_status ON outreach_drafts(status);

-- Durable Outreach Queue Table (Independent Autonomous Scheduling)
CREATE TABLE IF NOT EXISTS outreach_queue (
    queue_id TEXT PRIMARY KEY,
    professor_id TEXT NOT NULL REFERENCES professors(professor_id) ON DELETE CASCADE,
    draft_id TEXT NOT NULL REFERENCES outreach_drafts(draft_id) ON DELETE CASCADE,
    time_block_id TEXT NOT NULL,
    window_start_ist TEXT NOT NULL,
    window_end_ist TEXT NOT NULL,
    sequence_order INTEGER NOT NULL,
    status TEXT DEFAULT 'PENDING', -- PENDING, IN_PROGRESS, SENT, FAILED, SUPPRESSED, RECOVERED, SKIPPED
    attempts INTEGER DEFAULT 0,
    last_attempt_at TEXT,
    dispatched_at TEXT,
    error_message TEXT,
    batch_number INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_outreach_queue_status ON outreach_queue(status);
CREATE INDEX IF NOT EXISTS idx_outreach_queue_block ON outreach_queue(time_block_id);
CREATE INDEX IF NOT EXISTS idx_outreach_queue_prof ON outreach_queue(professor_id);
CREATE INDEX IF NOT EXISTS idx_outreach_queue_order ON outreach_queue(sequence_order);

-- Email Threads Table
CREATE TABLE IF NOT EXISTS email_threads (
    thread_id TEXT PRIMARY KEY,
    professor_id TEXT NOT NULL REFERENCES professors(professor_id) ON DELETE CASCADE,
    subject TEXT NOT NULL,
    status TEXT DEFAULT 'ACTIVE', -- ACTIVE, REPLIED, RESOLVED, CLOSED, SUPPRESSED
    message_count INTEGER DEFAULT 1,
    last_message_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_email_threads_prof ON email_threads(professor_id);

-- Email Messages Table
CREATE TABLE IF NOT EXISTS email_messages (
    message_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES email_threads(thread_id) ON DELETE CASCADE,
    professor_id TEXT REFERENCES professors(professor_id) ON DELETE SET NULL,
    direction TEXT NOT NULL, -- OUTBOUND, INBOUND
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    in_reply_to TEXT,
    references_header TEXT,
    sent_received_at TEXT NOT NULL,
    raw_headers TEXT
);

CREATE INDEX IF NOT EXISTS idx_email_messages_thread ON email_messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_email_messages_in_reply_to ON email_messages(in_reply_to);
CREATE INDEX IF NOT EXISTS idx_email_messages_sender ON email_messages(sender);

-- Reply Classifications Table
CREATE TABLE IF NOT EXISTS reply_classifications (
    classification_id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL UNIQUE REFERENCES email_messages(message_id) ON DELETE CASCADE,
    professor_id TEXT NOT NULL REFERENCES professors(professor_id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    confidence REAL NOT NULL,
    reasoning TEXT NOT NULL,
    action_required TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Human-Review Reply Drafts Table (AI CANNOT SEND)
CREATE TABLE IF NOT EXISTS reply_drafts (
    reply_draft_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES email_threads(thread_id) ON DELETE CASCADE,
    reply_to_message_id TEXT NOT NULL REFERENCES email_messages(message_id) ON DELETE CASCADE,
    professor_id TEXT NOT NULL REFERENCES professors(professor_id) ON DELETE CASCADE,
    draft_body TEXT NOT NULL,
    word_count INTEGER NOT NULL,
    status TEXT DEFAULT 'AWAITING_HUMAN_REVIEW', -- AWAITING_HUMAN_REVIEW, HUMAN_APPROVED, HUMAN_SENT, REJECTED
    created_at TEXT NOT NULL,
    human_approved_at TEXT,
    human_reviewer TEXT,
    human_sent_at TEXT
);

-- Follow-Up Drafts Table (AI CANNOT SEND)
CREATE TABLE IF NOT EXISTS followup_drafts (
    followup_draft_id TEXT PRIMARY KEY,
    professor_id TEXT NOT NULL REFERENCES professors(professor_id) ON DELETE CASCADE,
    original_message_id TEXT NOT NULL REFERENCES email_messages(message_id) ON DELETE CASCADE,
    draft_body TEXT NOT NULL,
    word_count INTEGER NOT NULL,
    scheduled_after_days INTEGER DEFAULT 7,
    status TEXT DEFAULT 'AWAITING_HUMAN_REVIEW', -- AWAITING_HUMAN_REVIEW, HUMAN_APPROVED, HUMAN_SENT, DISMISSED
    created_at TEXT NOT NULL,
    human_approved_at TEXT,
    human_reviewer TEXT,
    human_sent_at TEXT
);

-- Suppression List Table
CREATE TABLE IF NOT EXISTS suppression_list (
    suppression_id TEXT PRIMARY KEY,
    email TEXT UNIQUE,
    domain TEXT,
    reason TEXT NOT NULL, -- PREVIOUSLY_CONTACTED, INSTITUTIONAL_SELF, REPLY_RECEIVED, DECLINED, BOUNCED, OPTED_OUT, MANUAL
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_suppression_email ON suppression_list(email);
CREATE INDEX IF NOT EXISTS idx_suppression_domain ON suppression_list(domain);

-- Immutable Event Audit Log Table
CREATE TABLE IF NOT EXISTS event_log (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    operator_id TEXT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    details_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_event_log_entity ON event_log(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_event_log_type ON event_log(event_type);
"""


def get_db_connection(db_path: Path = None) -> sqlite3.Connection:
    """Return an open SQLite database connection with row factory configured."""
    path = db_path or settings.SQLITE_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_db(db_path: Path = None) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for SQLite connections handling commits and rollbacks."""
    conn = get_db_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_database(db_path: Path = None) -> None:
    """Execute schema script and seed mandatory suppressions with non-destructive migrations."""
    with get_db(db_path) as conn:
        # Pre-migration: if professors table already exists from an older schema, add new columns before creating index
        try:
            tbl = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='professors';").fetchone()
            if tbl:
                cols = {col["name"] for col in conn.execute("PRAGMA table_info(professors);").fetchall()}
                if "time_block" not in cols:
                    conn.execute("ALTER TABLE professors ADD COLUMN time_block TEXT;")
                if "time_block_order" not in cols:
                    conn.execute("ALTER TABLE professors ADD COLUMN time_block_order INTEGER;")
                if "iana_timezone" not in cols:
                    conn.execute("ALTER TABLE professors ADD COLUMN iana_timezone TEXT;")
        except Exception:
            pass

        conn.executescript(SCHEMA_SQL)
        seed_mandatory_suppressions(conn)


def seed_mandatory_suppressions(conn: sqlite3.Connection) -> None:
    """
    Ensure IIT Delhi, previously contacted external professors, and confirmed reply-received contacts
    are firmly suppressed from initial outreach.
    """
    initial_suppressions = [
        # 1. Institutional exclusion: IIT Delhi (self-institution)
        ("supp_domain_iitd", None, "iitd.ac.in", "INSTITUTIONAL_SELF", "Exclude IIT Delhi from external outreach"),
        ("supp_domain_chem_iitd", None, "chemical.iitd.ac.in", "INSTITUTIONAL_SELF", "Exclude IIT Delhi chemical engineering domain"),
        ("supp_email_student", "ch1251635@iitd.ac.in", None, "INSTITUTIONAL_SELF", "Student primary institutional address"),
        
        # 2. Previously contacted: Kai-Kit Wong (UCL)
        ("supp_kaikit_wong_ucl", "k.wong@ucl.ac.uk", None, "PREVIOUSLY_CONTACTED", "Prof. Kai-Kit Wong (UCL) already contacted; suppressed from fresh outreach"),
        ("supp_kaikit_wong_alt", "kai-kit.wong@ucl.ac.uk", None, "PREVIOUSLY_CONTACTED", "Prof. Kai-Kit Wong (UCL) alternative email alias"),

        # 3. Previously contacted: Chenshu Wu (HKU / Rutgers)
        ("supp_chenshu_wu_hku", "cwu@cs.hku.hk", None, "PREVIOUSLY_CONTACTED", "Prof. Chenshu Wu (HKU) already contacted; suppressed from fresh outreach"),
        ("supp_chenshu_wu_rutgers", "chenshu.wu@rutgers.edu", None, "PREVIOUSLY_CONTACTED", "Prof. Chenshu Wu (Rutgers) alternative institutional email"),

        # 4. Confirmed Reply-Received Contacts (Exclude from fresh initial outreach)
        ("supp_han_fan_tum", "han.fan@tum.de", None, "REPLY_RECEIVED", "Han Fan (TUM) confirmed reply received; excluded from initial outreach"),
        ("supp_hojin_lee_tum", "hojin.lee@tum.de", None, "REPLY_RECEIVED", "Hojin Lee (TUM) confirmed reply received; excluded from initial outreach"),
        ("supp_lukas_oehler_tum", "lukas.oehler@tum.de", None, "REPLY_RECEIVED", "Lukas Oehler (TUM) confirmed reply received; excluded from initial outreach"),
        ("supp_tjtkoski_kth", "tjtkoski@kth.se", None, "REPLY_RECEIVED", "Timo Koski (KTH) confirmed reply received; excluded from initial outreach"),
        ("supp_fontina_petrakopoulou_tub", "fontina.petrakopoulou@tu-berlin.de", None, "REPLY_RECEIVED", "Fontina Petrakopoulou (TU Berlin) confirmed reply received; excluded from initial outreach"),
        ("supp_info_sitegeist", "info@sitegeist-robotics.com", None, "REPLY_RECEIVED", "Sitegeist Robotics confirmed reply received; excluded from initial outreach"),
    ]

    # Clean up any legacy unsupported classifications
    conn.execute("DELETE FROM suppression_list WHERE email IN ('bodner@mit.edu', 'pliang@cs.stanford.edu');")

    for supp_id, email, domain, reason, notes in initial_suppressions:
        conn.execute("""
            INSERT INTO suppression_list (suppression_id, email, domain, reason, notes, created_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(suppression_id) DO UPDATE SET
                reason = excluded.reason,
                notes = excluded.notes;
        """, (supp_id, email.lower() if email else None, domain.lower() if domain else None, reason, notes))
