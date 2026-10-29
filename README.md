# International Research Outreach Intelligence Engine

> **Production-Grade, Local-First Outreach System for IIT Delhi Undergraduate Seeking Summer 2027 International Research Internships**

---

## 1. Executive Summary & Architecture

The **International Research Outreach Intelligence Engine** is an integrated software system engineered for **Harshith N**, an undergraduate in the Department of Chemical Engineering at the **Indian Institute of Technology Delhi (IIT Delhi)**.

Targeting elite international research groups across QS World University Rankings 2027 top-120 institutions, the engine replaces superficial bulk emailing with:
1. **Authoritative Master Data Reconciliation**: Dynamically imports and non-destructively synchronizes with `Summer_2027_QS_Top_120_Professor_Research_Database.xlsx` (1,288 faculty prospects across 121 universities).
2. **Personal Intelligence Base**: Grounded strictly in verified student projects (Neural Process Twin, LLM Inference Engine, PINN Error Estimation, C++17 Peng-Robinson Thermodynamics, Humanoid Robotics).
3. **Four-Paragraph Personalization Engine**: Produces concise 130–180 word emails bridging faculty research to substantiated student code, with strict signature integrity (no social links).
4. **Destination-Local Scheduler**: Targets professor-local weekday 8:00 AM – 9:00 AM arrival times across 13 regional clusters, mapped to operator IST (`Asia/Kolkata`) with date-dependent IANA zoneinfo and Daylight Saving Time (DST) calculations.
5. **Critical Permission Boundary**: Application-level security barring autonomous AI reply or follow-up transmissions. All replies and follow-ups require explicit, manual human review and approval.
6. **IIT Delhi Institutional Mail Integration**: Secure IMAP (`mailstore.iitd.ac.in:993`) and SMTP (`smtp.iitd.ac.in:465`) gateways with dry-run protection, disabled sending defaults, and permanent suppressions.

```
                             ┌──────────────────────────────────────────────┐
                             │    Authoritative Master Excel Workbook       │
                             │  (1,288 Faculty Prospects, 121 Universities) │
                             └──────────────────────┬───────────────────────┘
                                                    │ Dynamic Schema Import &
                                                    │ Atomic Sync (.bak & lock)
                                                    ▼
┌──────────────────────────┐         ┌──────────────────────────────────────┐
│ Personal Intelligence    │         │          SQLite WAL Database         │
│ Base (Verified Projects) │────────▶│  (Professors, Threads, Drafts, Logs) │
└──────────────────────────┘         └──────────────────┬───────────────────┘
                                                        │
                      ┌─────────────────────────────────┴───────────────────────────────┐
                      ▼                                                                 ▼
      ┌───────────────────────────────┐                                 ┌───────────────────────────────┐
      │     Personalization Engine    │                                 │     Time Block Scheduler      │
      │   (4 Paragraphs, 130-180 w)   │                                 │   (8:00-9:00 AM Local -> IST) │
      └───────────────┬───────────────┘                                 └───────────────┬───────────────┘
                      │                                                                 │
                      └─────────────────────────────────┬───────────────────────────────┘
                                                        ▼
                                      ┌───────────────────────────────────┐
                                      │       Outbound Sending Gateway    │
                                      │ (Default OFF • Dry-Run • Limit 5) │
                                      └─────────────────┬─────────────────┘
                                                        │
                      ┌─────────────────────────────────┴───────────────────────────────┐
                      ▼                                                                 ▼
      ┌───────────────────────────────┐                                 ┌───────────────────────────────┐
      │      IIT Delhi IMAP Sync      │                                 │   Reply Intelligence Engine   │
      │   (Read-Only INBOX Examine)   │                                 │ (10 Categories, Auto-Suppress)│
      └───────────────┬───────────────┘                                 └───────────────┬───────────────┘
                      │                                                                 │
                      └─────────────────────────────────┬───────────────────────────────┘
                                                        ▼
                                      ┌───────────────────────────────────┐
                                      │   CRITICAL PERMISSION BOUNDARY    │
                                      │   [AI CANNOT TRANSMIT REPLIES]    │
                                      └─────────────────┬─────────────────┘
                                                        │
                                                        ▼
                                      ┌───────────────────────────────────┐
                                      │     HUMAN OPERATOR DASHBOARD      │
                                      │  (Exclusive Review & Manual Send) │
                                      └───────────────────────────────────┘
```

---

## 2. Critical Permission Boundary & Safety Protocols

| Dimension | Autonomous AI Role | Human Operator Role |
| :--- | :--- | :--- |
| **First-Contact Outreach** | Drafts, validates word count, and schedules. | Approves drafts and initiates batch dispatch. |
| **Incoming Professor Replies** | Classifies into 10 categories and drafts contextual response. | **Exclusive** reviewer with sole permission to transmit replies. |
| **Follow-Up Inquiries** | Prepares draft after 7+ days of inactivity. | **Exclusive** reviewer; decides whether to follow up. |
| **Transmissions** | **STRUCTURALLY BARRED** at application code level (`PermissionDeniedError`). | Explicit manual button trigger with operator ID. |
| **Emergency Stop** | Cannot override emergency stop. | Global killswitch toggle halts all queues instantly. |

### Permanent Suppression Safeguards
Any recipient matching any of the following criteria is **permanently blocked** from initial outreach and queued follow-ups:
1. **Institutional Self**: `@iitd.ac.in`, `@chemical.iitd.ac.in` (IIT Delhi excluded from external outreach).
2. **Previously Contacted Faculty**: Prof. Kai-Kit Wong (`k.wong@ucl.ac.uk`, UCL) and Prof. Chenshu Wu (`cwu@cs.hku.hk`, HKU).
3. **Response Received**: Any professor reply (`INTEREST`, `MEETING_INVITE`, `INFO_REQUEST`, `REFERRAL`, `TECHNICAL_QUESTION`, `FUNDING_QUESTION`).
4. **Rejection or Capacity Full**: Any `REJECTION` automatically stops outreach.
5. **Delivery Failure / Opt-Out**: Any `BOUNCE` or `OPT_OUT` permanently halts communication.

---

## 3. Authoritative Data Model & Master Synchronization

The engine uses SQLite (`data/outreach_engine.db`) in **Write-Ahead Logging (WAL)** mode with durable professor IDs generated deterministically:
$$\text{professor\_id} = \text{"prof\_"} + \text{SHA256}(\text{university} \mathbin{\Vert} \text{name})[:16]$$

### Dynamic Schema Import
- Dynamically matches Excel headers without hardcoding column positions.
- Preserves all 1,288 rows, notes, and formulas.
- Never resets existing mailing statuses or historical context.

### Atomic Sync-Back Protocol
When updating the authoritative Excel workbook:
1. **File Locking**: Acquires atomic lockfile (`.lock`) to prevent concurrent process collisions.
2. **Pre-Sync Backup**: Automatically creates a timestamped copy:  
   `Summer_2027_QS_Top_120_Professor_Research_Database_pre_sync_YYYYMMDD_HHMMSS.xlsx.bak`.
3. **In-Place Preservation**: Retains all original sheets (`Professor Database`, `University Database`) and appends/updates `Outreach Status`.
4. **Time Blocks Generation**: Writes the `Time Blocks and Distribution` sheet with scheduled regional groups.
5. **Atomic Rename**: Saves to a temporary file (`.tmp_<pid>.xlsx`) and renames over the original file atomically.

---

## 4. Personal Intelligence Base & Verified Credentials

All generated correspondence is verified against substantiated source documents. **Never invent funding, positions, or achievements.**

- **Applicant Identity**: Harshith N, Undergraduate (B.Tech) in Chemical Engineering, IIT Delhi (Entry Number: `2025CH11635`).
- **Verified Institutional Email**: `ch1251635@iitd.ac.in`.
- **Coursework Milestones**:
  - `CHL2001` (Numerical Analysis for Chemical Engineers): Independent audit, reproduction, and soft-boundary extension of Krasowski et al. (arXiv:2603.15526) on PINN Finite-Difference Error Estimation.
  - `CHL2002` (Thermodynamics): Pure-component real-gas Peng-Robinson cubic equation of state solver in C++17 with analytical stability derivatives $(\partial P/\partial v)_T$ and departure functions.
- **Substantiated Software Projects**:
  1. **Neural Process Twin**: Physics-aware SciML surrogate simulator in PyTorch for 2D steady-state heat conduction, mapping parameter spaces to solution fields.
  2. **LLM Inference Intelligence Engine**: Measurement-driven LLM benchmarking platform profiling TTFT latency, TPOT, and Roofline model memory saturation.
  3. **Humanoid Robotics**: Embedded microcontrollers, servo-actuated multi-DOF kinematic mechanisms, and closed-loop control.
  4. **Founder Mindset**: AI-native systems builder focused on foundational computational performance and societal impact.

---

## 5. Four-Paragraph Personalization Engine (130–180 Words)

Every outreach email strictly follows the four-paragraph structure:
1. **Paragraph 1: Identity & Clear Purpose (25–35 words)**: Harshith N, Chemical Engineering undergraduate at IIT Delhi, seeking a research internship for Summer 2027.
2. **Paragraph 2: Faculty Research Relevance (35–50 words)**: Specific, defensible connection to the professor's recent publications or lab focus from the verified database.
3. **Paragraph 3: Substantive Project Bridge (45–65 words)**: Concrete technical bridge to Neural Process Twin, LLM Inference Engine, PINN error estimation, C++17 Thermo, or Humanoid robotics.
4. **Paragraph 4: Summer 2027 Request & Close (25–35 words)**: Inquiring about a 10–12 week internship in Summer 2027 and offering a brief call.

### Strict Signature
The signature is strictly formatted without LinkedIn or GitHub links:
```text
Sincerely,
Harshith N
Undergraduate, Department of Chemical Engineering
Indian Institute of Technology Delhi (IIT Delhi)
ch1251635@iitd.ac.in
```

---

## 6. IST Scheduler & Regional Time Blocks

The scheduling target is **destination-local weekday 8:00 AM – 9:00 AM**. Times are calculated dynamically using IANA `zoneinfo` accounting for Daylight Saving Time (DST) transitions, then mapped to operator IST (`Asia/Kolkata`).

| Block ID | Region / Cluster | Reference IST Dispatch | Destination Arrival |
| :--- | :--- | :--- | :--- |
| `BLOCK_01_JP_KR` | Japan & South Korea | 4:30 AM – 5:00 AM IST | 8:00 AM JST / KST (UTC+9) |
| `BLOCK_02_SG_CN_HK_TW` | Singapore, China, Hong Kong, Taiwan | 5:30 AM IST | 8:00 AM SGT / CST / HKT (UTC+8) |
| `BLOCK_03_SEA` | Southeast Asia (Malaysia, Thailand) | 6:30 AM IST | 8:00 AM ICT (UTC+7) |
| `BLOCK_04_GULF` | United Arab Emirates & Gulf | 9:30 AM IST | 8:00 AM GST (UTC+4) |
| `BLOCK_05_SA_TR_RU` | Saudi Arabia, Turkey, Moscow | 10:30 AM IST | 8:00 AM AST / TRT / MSK (UTC+3) |
| `BLOCK_06_EAST_EU` | Eastern Europe | 11:30 AM IST (winter) / 10:30 AM (summer) | 8:00 AM EET / EEST (UTC+2/UTC+3) |
| `BLOCK_07_CONT_EU` | Continental Europe (DE, CH, FR, NL, SE, DK) | 12:30 PM IST (winter) / 11:30 AM (summer) | 8:00 AM CET / CEST (UTC+1/UTC+2) |
| `BLOCK_08_UK_IE_PT` | United Kingdom, Ireland, Portugal | 1:30 PM IST (winter) / 12:30 PM (summer) | 8:00 AM GMT / BST (UTC+0/UTC+1) |
| `BLOCK_09_US_EAST` | US Eastern & Canada Eastern | 6:30 PM IST (winter) / 5:30 PM (summer) | 8:00 AM EST / EDT (UTC-5/UTC-4) |
| `BLOCK_10_US_CENTRAL`| US Central & Canada Central | 7:30 PM IST (winter) / 6:30 PM (summer) | 8:00 AM CST / CDT (UTC-6/UTC-5) |
| `BLOCK_11_US_MOUNTAIN`| US Mountain & Canada Mountain | 8:30 PM IST (winter) / 7:30 PM (summer) | 8:00 AM MST / MDT (UTC-7/UTC-6) |
| `BLOCK_12_US_PACIFIC` | US Pacific & Canada Pacific | 9:30 PM IST (winter) / 8:30 PM (summer) | 8:00 AM PST / PDT (UTC-8/UTC-7) |
| `BLOCK_13_OCEANIA` | Australia & New Zealand | 2:30 AM – 3:30 AM IST | 8:00 AM AEST / NZST |

---

## 7. Reply Intelligence & 10-Category Classification

Incoming messages synchronized via IMAP are matched to threads by `Message-ID`, `In-Reply-To`, and `References`. Responses are classified into 10 distinct categories:

1. `INTEREST`: Positive feedback; prepares scheduling alignment draft.
2. `MEETING_INVITE`: Interview/Zoom invitation; prepares meeting coordination draft.
3. `INFO_REQUEST`: Requests CV or transcripts; prepares document submission draft.
4. `REFERRAL`: Referred to portal or colleague; prepares acknowledgment draft.
5. `TECHNICAL_QUESTION`: Technical inquiries; prepares project clarification draft.
6. `FUNDING_QUESTION`: Funding inquiries; prepares factual grant clarification draft.
7. `AUTO_REPLY`: Out-of-office; logs away status, keeps lead open.
8. `REJECTION`: Lab full/no positions; halts outreach, archives as `DECLINED`.
9. `BOUNCE`: Delivery failure (550 User Unknown); permanently suppresses address.
10. `OPT_OUT`: Unsubscribe request; permanently suppresses address.

---

## 8. Installation, Testing & Startup Instructions

### Prerequisites
- Python 3.12+ (tested with Python 3.12.10 on Windows)
- Dependencies: `fastapi`, `uvicorn`, `pydantic`, `openpyxl`, `tzdata`, `pytest`

### Step 1: Environment Configuration
Copy `.env.example` to `.env`:
```powershell
Copy-Item .env.example .env
```
Configure your credentials in `.env` (or leave empty for default dry-run mode):
```ini
IITD_EMAIL_USER=ch1251635@iitd.ac.in
IITD_EMAIL_PASSWORD=your_kerberos_password_here
GATEWAY_ENABLED=False
DRY_RUN=True
```

### Step 2: Run Full Test Suite
Execute the 30 unit and integration tests:
```powershell
python -m pytest
```
*Expected output: `30 passed in ~3.8s`.*

### Step 3: Run Demonstration Script
Verify the end-to-end pipeline in dry-run mode:
```powershell
python scripts/run_demo.py
```

### Step 4: Launch Operator Dashboard
Start the local FastAPI application server:
```powershell
python -m uvicorn src.web.app:app --host 127.0.0.1 --port 8000 --reload
```
Open your browser and navigate to:
**`http://127.0.0.1:8000`**

---

## 9. Activation Checklist for Real Email Transmission

To transition from Session 01 dry-run mode to live outreach in future sessions, complete the following formal gates:

1. **IIT Delhi CSC Automation Approval**: Obtain formal authorization from IIT Delhi Computer Services Centre (CSC) confirming unattended IMAP/SMTP programmatic access compliance.
2. **Operator Campaign Authorization**: The student must explicitly review and authorize the batch.
3. **Quality Gates Sign-Off**: 100% of drafts must pass word count (130–180 words), research relevance verification, and signature audits.
4. **Sending Limits Configuration**: Enforce `MAX_EMAILS_PER_BATCH=5`, `MAX_EMAILS_PER_DAY=25`, and `MIN_INTERVAL=30s`.
5. **Gateway Activation**: Set `GATEWAY_ENABLED=True`, `DRY_RUN=False`, and `CAMPAIGN_AUTHORIZED=True` in `.env`.

---

## 10. Project Directory Structure

```text
Outreach Intelligence Engine/
├── .env.example                       # Environment configuration template
├── .gitignore                         # Strict exclusion of credentials, DBs, and CVs
├── pyproject.toml                     # Python package metadata and pytest config
├── README.md                          # Comprehensive system documentation
├── config/
│   ├── settings.py                    # Pydantic settings with safety defaults
│   └── university_timezones.json      # IANA timezone mappings for QS Top-120
├── data/
│   ├── knowledge_base/
│   │   ├── student_profile.json       # Verified identity, degree, and milestones
│   │   ├── verified_projects.json     # Substantiated projects (SciML, LLM, etc.)
│   │   └── outreach_guidelines.md     # 4-paragraph structure & tone guidelines
├── src/
│   ├── database/
│   │   ├── connection.py              # SQLite connection in WAL mode & initial suppressions
│   │   ├── models.py                  # Dataclass entities (Professor, Thread, Draft, etc.)
│   │   ├── repository.py              # CRUD, durable ID hashing, suppression checks
│   │   └── workbook_sync.py           # Dynamic schema import & atomic sync-back
│   ├── intelligence/
│   │   ├── knowledge_base.py          # Knowledge base loader
│   │   ├── personalization.py         # 4-paragraph personalization engine (130-180 w)
│   │   ├── reply_classifier.py        # 10-category reply classifier
│   │   └── reply_drafter.py           # Human-review reply and follow-up drafter
│   ├── mail/
│   │   ├── credentials.py             # Secure credential retrieval & masking
│   │   ├── imap_client.py             # Secure read-only IMAP inspection (port 993)
│   │   ├── smtp_client.py             # Secure authenticated SMTP delivery (port 465)
│   │   ├── thread_matcher.py          # Message-ID, In-Reply-To, and thread correlation
│   │   └── sending_gateway.py         # Outbound gateway with human-only reply guards
│   ├── scheduler/
│   │   ├── time_blocks.py             # Regional time block aggregator
│   │   └── timezone_engine.py         # IANA zoneinfo & DST calculation engine
│   ├── security/
│   │   ├── audit_logger.py            # Immutable append-only audit trail
│   │   └── permissions.py             # Application-level permission boundaries
│   └── web/
│       ├── app.py                     # FastAPI application server
│       ├── routes/api.py              # REST API endpoints
│       └── static/                    # Operator Dashboard SPA (HTML, CSS, JS)
├── tests/                             # 30 comprehensive unit & integration tests
│   ├── test_end_to_end.py
│   ├── test_permissions_and_boundary.py
│   ├── test_personalization.py
│   ├── test_reply_classifier.py
│   ├── test_scheduler_and_timezones.py
│   ├── test_sending_gateway.py
│   ├── test_thread_matcher.py
│   └── test_workbook_sync.py
└── scripts/
    ├── import_master_workbook.py      # Standalone CLI reconciliation script
    └── run_demo.py                    # Working local demonstration script
```
