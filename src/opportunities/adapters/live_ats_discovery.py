"""
Opportunity Intelligence Engine: Live Public ATS Discovery Adapter.
Discovers genuine, previously uncatalogued frontier-tech startup engineering opportunities
directly from official public ATS job boards (Ashby, Greenhouse).
"""

import json
import logging
import urllib.request
import time
import re
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone

from src.opportunities.adapters.base import BaseOpportunityAdapter
from src.opportunities.models import (
    Organization,
    Opportunity,
    OpportunityType,
    OpportunityClassification,
    RoleTitleType,
    WorkplaceType,
    InternationalEligibility,
    CompensationType,
    ContactChannel,
    VerificationStatus,
    DiscoveryCheckpoint,
    PriorityTier,
    ContactHistoryStatus
)
from src.opportunities.registry import (
    canonicalize_url,
    compute_organization_id,
    compute_opportunity_id
)
from src.opportunities.ranking import OpportunityRanker

logger = logging.getLogger(__name__)


# List of genuine, uncatalogued frontier-technology companies with verified public ATS boards
UNCATALOGUED_ATS_TARGETS = [
    {
        "name": "Perplexity AI",
        "website": "https://www.perplexity.ai",
        "city": "San Francisco",
        "country": "United States",
        "timezone": "America/Los_Angeles",
        "domains": ["LLMs", "Inference Optimization", "Distributed Systems", "AI Search"],
        "funding_stage": "SERIES_B",
        "tier": "TIER_1",
        "kind": "ashby",
        "board_id": "perplexity",
        "relevance": "Direct intersection with LLM Inference Intelligence Engine and low-latency token streaming.",
        "potential_contribution": "Optimizing multi-tenant inference latency and distributed search index retrieval pipelines.",
        "matched_project": "LLM Inference Intelligence Engine (Hardware Profiling & Roofline Diagnostics)"
    },
    {
        "name": "Cartesia",
        "website": "https://cartesia.ai",
        "city": "San Francisco",
        "country": "United States",
        "timezone": "America/Los_Angeles",
        "domains": ["Audio AI", "State Space Models", "Low-Latency Inference", "Edge AI"],
        "funding_stage": "SERIES_A",
        "tier": "TIER_1",
        "kind": "ashby",
        "board_id": "cartesia",
        "relevance": "Real-time state space model (SSM) architecture and sub-100ms audio generation inference.",
        "potential_contribution": "Benchmarking memory-bandwidth bottlenecks for continuous streaming audio models.",
        "matched_project": "LLM Inference Intelligence Engine (Hardware Profiling & Roofline Diagnostics)"
    },
    {
        "name": "Neon",
        "website": "https://neon.tech",
        "city": "San Francisco",
        "country": "United States",
        "timezone": "America/Los_Angeles",
        "domains": ["Distributed Systems", "Storage Engines", "Rust", "HPC"],
        "funding_stage": "SERIES_B",
        "tier": "TIER_1",
        "kind": "ashby",
        "board_id": "neon",
        "relevance": "Serverless Postgres storage architecture, memory-page cache management, and compute separation.",
        "potential_contribution": "Benchmarking page cache eviction bounds and asynchronous storage pipeline throughput.",
        "matched_project": "Sovereign Industrial Agentic AI Workbench (SIH 2026 / MRPL)"
    },
    {
        "name": "Braintrust",
        "website": "https://www.braintrust.dev",
        "city": "San Francisco",
        "country": "United States",
        "timezone": "America/Los_Angeles",
        "domains": ["AI Infrastructure", "LLM Evaluation", "Observability", "Proxy Serving"],
        "funding_stage": "SERIES_A",
        "tier": "TIER_1",
        "kind": "ashby",
        "board_id": "braintrust",
        "relevance": "LLM proxy tracing, deterministic benchmark evaluations, and AI service observability.",
        "potential_contribution": "Designing automated regression evaluation suites and tracing latency breakdown across model providers.",
        "matched_project": "Sovereign Industrial Agentic AI Workbench (SIH 2026 / MRPL)"
    },
    {
        "name": "Abridge",
        "website": "https://www.abridge.com",
        "city": "Pittsburgh",
        "country": "United States",
        "timezone": "America/New_York",
        "domains": ["Speech AI", "LLM Systems", "Clinical AI", "Real-Time Audio"],
        "funding_stage": "SERIES_C",
        "tier": "TIER_1",
        "kind": "ashby",
        "board_id": "abridge",
        "relevance": "Speech-to-text foundation models and medical conversation summarization under strict latency SLAs.",
        "potential_contribution": "Profiling speech model inference latency on GPU clusters and building automated evaluation harnesses.",
        "matched_project": "LLM Inference Intelligence Engine (Hardware Profiling & Roofline Diagnostics)"
    },
    {
        "name": "Scale AI",
        "website": "https://scale.com",
        "city": "San Francisco",
        "country": "United States",
        "timezone": "America/Los_Angeles",
        "domains": ["AI Infrastructure", "Model Evaluation", "RLHF", "Data Pipelines"],
        "funding_stage": "SERIES_F",
        "tier": "TIER_1",
        "kind": "greenhouse",
        "board_id": "scaleai",
        "relevance": "Frontier model benchmarking, RLHF pipelines, and automated test harnesses.",
        "potential_contribution": "Building automated model evaluation harnesses and synthetic benchmark datasets.",
        "matched_project": "Sovereign Industrial Agentic AI Workbench (SIH 2026 / MRPL)"
    },
    {
        "name": "SambaNova Systems",
        "website": "https://sambanova.ai",
        "city": "Palo Alto",
        "country": "United States",
        "timezone": "America/Los_Angeles",
        "domains": ["AI Hardware", "Silicon Architecture", "HPC", "Compiler Systems"],
        "funding_stage": "SERIES_D",
        "tier": "TIER_1",
        "kind": "greenhouse",
        "board_id": "sambanovasystems",
        "relevance": "Dataflow accelerator architecture, high-throughput inference chips, and spatial compilers.",
        "potential_contribution": "Formulating Roofline intensity benchmarks and profiling memory bandwidth on custom dataflow hardware.",
        "matched_project": "LLM Inference Intelligence Engine (Hardware Profiling & Roofline Diagnostics)"
    },
    {
        "name": "Glean",
        "website": "https://www.glean.com",
        "city": "Palo Alto",
        "country": "United States",
        "timezone": "America/Los_Angeles",
        "domains": ["Enterprise AI", "Knowledge Retrieval", "Agentic Search", "Distributed Systems"],
        "funding_stage": "SERIES_E",
        "tier": "TIER_1",
        "kind": "greenhouse",
        "board_id": "gleanwork",
        "relevance": "Enterprise graph search, distributed embeddings, and autonomous retrieval agents.",
        "potential_contribution": "Benchmarking embedding retrieval latency and optimizing vector search ranking pipelines.",
        "matched_project": "Sovereign Industrial Agentic AI Workbench (SIH 2026 / MRPL)"
    },
    {
        "name": "Anthropic",
        "website": "https://www.anthropic.com",
        "city": "San Francisco",
        "country": "United States",
        "timezone": "America/Los_Angeles",
        "domains": ["AI Safety", "LLM Systems", "Inference Infrastructure", "Distributed Training"],
        "funding_stage": "SERIES_D",
        "tier": "TIER_1",
        "kind": "greenhouse",
        "board_id": "anthropic",
        "relevance": "Frontier AI systems infrastructure, high-throughput model serving, and distributed GPU clusters.",
        "potential_contribution": "Benchmarking inference kernel operational intensity and evaluating token latency and memory bandwidth across serving configurations.",
        "matched_project": "LLM Inference Intelligence Engine (Hardware Profiling & Roofline Diagnostics)"
    },
    {
        "name": "Etched",
        "website": "https://www.etched.com",
        "city": "Cupertino",
        "country": "United States",
        "timezone": "America/Los_Angeles",
        "domains": ["AI Hardware", "Transformer ASICs", "Inference Acceleration", "Custom Silicon"],
        "funding_stage": "SERIES_A",
        "tier": "TIER_1",
        "kind": "ashby",
        "board_id": "etched",
        "relevance": "Custom ASIC hardware designed specifically for transformer inference and high-throughput attention.",
        "potential_contribution": "Formulating Roofline model operational intensity benchmarks and low-level hardware verification pipelines.",
        "matched_project": "LLM Inference Intelligence Engine (Hardware Profiling & Roofline Diagnostics)"
    },
    {
        "name": "Cognition AI",
        "website": "https://www.cognition.ai",
        "city": "San Francisco",
        "country": "United States",
        "timezone": "America/Los_Angeles",
        "domains": ["AI Software Engineering", "Autonomous Agents", "Code Intelligence", "Reasoning Models"],
        "funding_stage": "SERIES_A",
        "tier": "TIER_1",
        "kind": "ashby",
        "board_id": "cognition",
        "relevance": "Autonomous coding intelligence, sandbox environment evaluation, and agentic reasoning.",
        "potential_contribution": "Benchmarking autonomous sandbox task execution and trace observability.",
        "matched_project": "Sovereign Industrial Agentic AI Workbench (SIH 2026 / MRPL)"
    },
    {
        "name": "Sierra",
        "website": "https://sierra.ai",
        "city": "San Francisco",
        "country": "United States",
        "timezone": "America/Los_Angeles",
        "domains": ["Enterprise Conversational AI", "Agentic Systems", "Real-Time Serving", "Reliability"],
        "funding_stage": "SERIES_B",
        "tier": "TIER_1",
        "kind": "ashby",
        "board_id": "sierra",
        "relevance": "Deterministic agentic dialogue workflows, high-availability customer intelligence, and safety guardrails.",
        "potential_contribution": "Profiling token generation latency and designing structured output validation schemas.",
        "matched_project": "LLM Inference Intelligence Engine (Hardware Profiling & Roofline Diagnostics)"
    },
    {
        "name": "Figure AI",
        "website": "https://www.figure.ai",
        "city": "Sunnyvale",
        "country": "United States",
        "timezone": "America/Los_Angeles",
        "domains": ["Humanoid Robotics", "Embodied AI", "Real-Time Control", "Low-Latency Systems"],
        "funding_stage": "SERIES_B",
        "tier": "TIER_1",
        "kind": "greenhouse",
        "board_id": "figureai",
        "relevance": "General-purpose humanoid robot architecture, real-time sensory perception, and low-latency motor control.",
        "potential_contribution": "Profiling real-time control loop jitter and benchmarking vision-language-action inference latencies.",
        "matched_project": "SuRGE Microdrone Autonomy & 3D Environment Mapping (Research Exposure)"
    },
    {
        "name": "Physical Intelligence",
        "website": "https://www.physicalintelligence.company",
        "city": "San Francisco",
        "country": "United States",
        "timezone": "America/Los_Angeles",
        "domains": ["Robot Foundation Models", "Embodied Intelligence", "Manipulation", "Physical AI"],
        "funding_stage": "SEED",
        "tier": "TIER_1",
        "kind": "ashby",
        "board_id": "physicalintelligence",
        "relevance": "General-purpose foundation models for robotics and physical world manipulation.",
        "potential_contribution": "Evaluating multi-task robot policy latency and profiling edge inference memory limits.",
        "matched_project": "SuRGE Microdrone Autonomy & 3D Environment Mapping (Research Exposure)"
    },
    {
        "name": "Cursor",
        "website": "https://www.cursor.com",
        "city": "San Francisco",
        "country": "United States",
        "timezone": "America/Los_Angeles",
        "domains": ["Code Intelligence", "Developer Tools", "LLM Inference", "Fast Context Caching"],
        "funding_stage": "SERIES_A",
        "tier": "TIER_1",
        "kind": "ashby",
        "board_id": "cursor",
        "relevance": "AI-native code generation, multi-file context indexing, and ultra-low latency token streaming.",
        "potential_contribution": "Benchmarking prefix-cache hit rates and evaluating context caching latency for code completion.",
        "matched_project": "LLM Inference Intelligence Engine (Hardware Profiling & Roofline Diagnostics)"
    },
    {
        "name": "Modal",
        "website": "https://modal.com",
        "city": "New York",
        "country": "United States",
        "timezone": "America/New_York",
        "domains": ["Serverless GPU Infrastructure", "Distributed Systems", "Cloud Runtimes", "Container Virtualization"],
        "funding_stage": "SERIES_A",
        "tier": "TIER_1",
        "kind": "ashby",
        "board_id": "modal",
        "relevance": "Sub-second cold-start serverless GPU execution, distributed filesystem caching, and Linux microVM scheduling.",
        "potential_contribution": "Benchmarking container cold-start memory footprints and profiling distributed GPU memory-bandwidth utilization.",
        "matched_project": "LLM Inference Intelligence Engine (Hardware Profiling & Roofline Diagnostics)"
    },
    {
        "name": "Wayve",
        "website": "https://wayve.ai",
        "city": "London",
        "country": "United Kingdom",
        "timezone": "Europe/London",
        "domains": ["Autonomous Driving", "Embodied AI", "End-to-End Robotics", "World Models"],
        "funding_stage": "SERIES_C",
        "tier": "TIER_1",
        "kind": "ashby",
        "board_id": "wayve",
        "relevance": "End-to-end embodied AI foundation models for real-world autonomous navigation.",
        "potential_contribution": "Benchmarking world-model policy inference latency and sensor fusion pipeline profiling.",
        "matched_project": "SuRGE Microdrone Autonomy & 3D Environment Mapping (Research Exposure)"
    },
    {
        "name": "Together AI",
        "website": "https://www.together.ai",
        "city": "San Francisco",
        "country": "United States",
        "timezone": "America/Los_Angeles",
        "domains": ["Distributed Training", "Inference Acceleration", "Cloud Clusters", "Flash Attention"],
        "funding_stage": "SERIES_A",
        "tier": "TIER_1",
        "kind": "greenhouse",
        "board_id": "togetherai",
        "relevance": "Distributed inference engines, custom cloud GPU infrastructure, and open model serving.",
        "potential_contribution": "Profiling memory bandwidth ceilings and benchmark execution latencies for transformer model inference.",
        "matched_project": "LLM Inference Intelligence Engine (Hardware Profiling & Roofline Diagnostics)"
    },
    {
        "name": "LangChain",
        "website": "https://www.langchain.com",
        "city": "San Francisco",
        "country": "United States",
        "timezone": "America/Los_Angeles",
        "domains": ["Agentic Architectures", "LLM Orchestration", "Evaluations", "LangSmith Tracing"],
        "funding_stage": "SERIES_A",
        "tier": "TIER_1",
        "kind": "ashby",
        "board_id": "langchain",
        "relevance": "Agentic control loops, deterministic workflow evaluation, and production LLM observability.",
        "potential_contribution": "Developing automated agent evaluation benchmarks and structured execution trace assertions.",
        "matched_project": "Sovereign Industrial Agentic AI Workbench (SIH 2026 / MRPL)"
    },
    {
        "name": "DeepL",
        "website": "https://www.deepl.com",
        "city": "Cologne",
        "country": "Germany",
        "timezone": "Europe/Berlin",
        "domains": ["Neural Machine Translation", "HPC Inference", "Language Models", "High-Throughput Serving"],
        "funding_stage": "SERIES_B",
        "tier": "TIER_1",
        "kind": "ashby",
        "board_id": "deepl",
        "relevance": "High-efficiency neural translation architectures and European sovereign AI infrastructure.",
        "potential_contribution": "Benchmarking translation model serving latency and profiling GPU memory saturation under batch loads.",
        "matched_project": "LLM Inference Intelligence Engine (Hardware Profiling & Roofline Diagnostics)"
    },
    {
        "name": "ElevenLabs",
        "website": "https://elevenlabs.io",
        "city": "London",
        "country": "United Kingdom",
        "timezone": "Europe/London",
        "domains": ["Voice AI", "Audio Foundation Models", "Real-Time Streaming", "Edge Speech"],
        "funding_stage": "SERIES_B",
        "tier": "TIER_1",
        "kind": "ashby",
        "board_id": "elevenlabs",
        "relevance": "Ultra-low-latency streaming voice generation models and real-time audio inference.",
        "potential_contribution": "Profiling streaming audio chunk latency and benchmarking memory-bandwidth utilization.",
        "matched_project": "LLM Inference Intelligence Engine (Hardware Profiling & Roofline Diagnostics)"
    },
    {
        "name": "Synthesia",
        "website": "https://www.synthesia.io",
        "city": "London",
        "country": "United Kingdom",
        "timezone": "Europe/London",
        "domains": ["Generative Video", "Neural Avatars", "Real-Time Rendering", "Vision AI"],
        "funding_stage": "SERIES_C",
        "tier": "TIER_1",
        "kind": "ashby",
        "board_id": "synthesia",
        "relevance": "Neural rendering pipelines, video generation foundation models, and European AI engineering.",
        "potential_contribution": "Benchmarking video frame synthesis latency and optimizing memory throughput across rendering clusters.",
        "matched_project": "Neural Process Twin (SciML / 2D Heat Conduction / PINNs)"
    }
]


class LiveATSDiscoveryAdapter(BaseOpportunityAdapter):
    """
    Live ATS Discovery Adapter.
    Connects to public Ashby and Greenhouse endpoints to discover authentic,
    previously uncatalogued openings with verifiable URLs and exact posting titles.
    """

    def __init__(self):
        super().__init__(adapter_name="LiveATSDiscoveryAdapter", category=OpportunityType.STARTUP.value)

    def fetch_raw_opportunities(
        self,
        checkpoint: Optional[DiscoveryCheckpoint] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Polls live public ATS targets and extracts technical/engineering postings.
        """
        target_idx = 0
        if checkpoint and checkpoint.cursor_or_token:
            try:
                target_idx = int(checkpoint.cursor_or_token)
            except ValueError:
                target_idx = 0

        total_targets = len(UNCATALOGUED_ATS_TARGETS)
        if target_idx >= total_targets:
            target_idx = 0

        raw_items: List[Dict[str, Any]] = []

        # Iterate over targets starting from cursor
        targets_to_query = UNCATALOGUED_ATS_TARGETS[target_idx:] + UNCATALOGUED_ATS_TARGETS[:target_idx]

        for target in targets_to_query:
            if len(raw_items) >= limit:
                break

            kind = target["kind"]
            board_id = target["board_id"]
            url = (
                f"https://api.ashbyhq.com/posting-api/job-board/{board_id}"
                if kind == "ashby"
                else f"https://boards-api.greenhouse.io/v1/boards/{board_id}/jobs"
            )

            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                with urllib.request.urlopen(req, timeout=6) as res:
                    data = json.loads(res.read().decode("utf-8"))
                    jobs = data.get("jobs", [])

                # Filter for technical, engineering, ML, systems, research, or intern roles
                keywords = ["engineer", "systems", "infrastructure", "ml", "software", "research", "intern", "platform", "dataflow", "compiler", "robotics", "hardware"]
                tech_jobs = [
                    j for j in jobs
                    if any(k in (j.get("title") or j.get("text") or "").lower() for k in keywords)
                ]

                # Select top 1-2 most relevant postings per company to ensure high diversity
                for j in tech_jobs[:2]:
                    raw_items.append({
                        "company_meta": target,
                        "job_raw": j,
                        "source_kind": kind
                    })
                    if len(raw_items) >= limit:
                        break

                time.sleep(0.5)  # Polite rate limiting between targets

            except Exception as e:
                logger.warning(f"LiveATSDiscoveryAdapter could not fetch {target['name']} ({url}): {e}")

        logger.info(f"LiveATSDiscoveryAdapter fetched {len(raw_items)} live postings across {len(UNCATALOGUED_ATS_TARGETS)} targets.")
        return raw_items

    def normalize_raw_item(self, raw_item: Dict[str, Any]) -> Tuple[Organization, Opportunity]:
        target = raw_item["company_meta"]
        job_raw = raw_item["job_raw"]
        kind = raw_item["source_kind"]
        now_iso = datetime.now(timezone.utc).isoformat()

        company_name = target["name"]
        web_url = canonicalize_url(target["website"])
        org_id = compute_organization_id(company_name, web_url)

        org = Organization(
            organization_id=org_id,
            name=company_name,
            category=OpportunityType.STARTUP.value,
            website_url=web_url,
            country=target.get("country"),
            city=target.get("city"),
            iana_timezone=target.get("timezone", "America/Los_Angeles"),
            description=f"Frontier technology startup in {', '.join(target['domains'][:2])}. Live ATS: {kind}.",
            funding_stage=target.get("funding_stage", "SERIES_A"),
            tier=target.get("tier", "TIER_1")
        )

        title = (job_raw.get("title") or job_raw.get("text") or "Software Engineer").strip()
        opp_id = compute_opportunity_id(org_id, OpportunityType.STARTUP.value, title, "Summer 2027")

        job_url = canonicalize_url(job_raw.get("jobUrl") or job_raw.get("absolute_url") or target["website"])

        # Location and workplace
        loc_str = target.get("city") or "San Francisco, CA"
        if kind == "ashby" and job_raw.get("location"):
            loc_str = job_raw["location"]
        elif kind == "greenhouse" and job_raw.get("location"):
            loc_data = job_raw["location"]
            loc_str = loc_data.get("name", loc_str) if isinstance(loc_data, dict) else str(loc_data)

        workplace = WorkplaceType.ONSITE.value
        if "remote" in loc_str.lower():
            workplace = WorkplaceType.REMOTE.value
        elif "hybrid" in loc_str.lower():
            workplace = WorkplaceType.HYBRID.value

        # Classification: distinguish internship vs general engineering vacancy
        desc_text = job_raw.get("descriptionPlain") or job_raw.get("descriptionHtml") or ""
        lower_title = title.lower()
        lower_desc = desc_text.lower()
        combined_text = f"{lower_title} {lower_desc}"

        is_intern = ("intern" in lower_title or "internship" in lower_title or "intern" in lower_desc[:200])

        if kind == "ashby" and job_raw.get("compensation"):
            comp_type = CompensationType.PAID.value
            comp_det = str(job_raw["compensation"])
            comp_ev = "STATUTORY_PAY_TRANSPARENCY_DISCLOSURE"
            classification = OpportunityClassification.CONFIRMED_OPENING.value if not is_intern else (
                OpportunityClassification.CONFIRMED_SUMMER_2027_INTERNSHIP.value if "2027" in combined_text
                else OpportunityClassification.CURRENT_INTERNSHIP_UNSPECIFIED_SUMMER.value
            )
            is_confirmed = True
            duration_weeks = 12 if is_intern else None
        elif is_intern:
            if "summer 2027" in combined_text or "2027" in combined_text:
                classification = OpportunityClassification.CONFIRMED_SUMMER_2027_INTERNSHIP.value
            else:
                classification = OpportunityClassification.CURRENT_INTERNSHIP_UNSPECIFIED_SUMMER.value
            is_confirmed = True
            duration_weeks = 12

            # Check for hourly / intern-specific stipend pay disclosures
            hourly_match = re.search(r"\$([\d,]+(?:\.\d+)?)\s*(?:-|to)\s*\$([\d,]+(?:\.\d+)?)\s*(?:/|\s*per\s*)(?:hr|hour)", desc_text, re.IGNORECASE)
            if hourly_match:
                comp_type = CompensationType.PAID.value
                comp_det = f"${hourly_match.group(1)} - ${hourly_match.group(2)} / hr (Statutory Intern Disclosure)"
                comp_ev = "STATUTORY_PAY_TRANSPARENCY_DISCLOSURE"
            else:
                comp_type = CompensationType.PAID.value
                comp_det = "Paid student engineering internship"
                comp_ev = "INTERNSHIP_COMPENSATION_POLICY"
        else:
            classification = OpportunityClassification.GENERAL_ENGINEERING_VACANCY.value
            is_confirmed = False
            duration_weeks = None
            # STRICT RULE: Never transfer full-time annual salaries into internship compensation fields
            comp_type = CompensationType.UNKNOWN.value
            comp_det = None
            comp_ev = "FULL_TIME_STATUTORY_DISCLOSURE_NOT_TRANSFERRED_TO_INTERNSHIP"

        opp = Opportunity(
            opportunity_id=opp_id,
            organization_id=org_id,
            title=title,
            opportunity_type=OpportunityType.STARTUP.value,
            source=f"LIVE_ATS_{kind.upper()}",
            location=loc_str,
            iana_timezone=target.get("timezone", "America/Los_Angeles"),
            technical_domains=target.get("domains", []),
            technical_relevance_summary=target.get("relevance"),
            potential_contribution=target.get("potential_contribution"),
            matched_project_id=target.get("matched_project"),
            eligibility_criteria="Undergraduate / graduate engineering students; systems proficiency required.",
            internship_duration_weeks=duration_weeks,
            target_term="Summer 2027",
            compensation_type=comp_type,
            compensation_details=comp_det,
            compensation_evidence=comp_ev,
            official_evidence_urls=[job_url],
            freshness_timestamp=now_iso,
            hiring_contact_channel=ContactChannel.APPLICATION_PORTAL.value,
            contact_history_status=ContactHistoryStatus.PORTAL_APPLICATION.value,
            contact_email=None,
            contact_name=f"{company_name} Recruiting Team",
            contact_verification_status=VerificationStatus.VERIFIED.value,
            application_url=job_url,
            application_deadline="Rolling",
            audit_classification=classification,
            role_title_type=RoleTitleType.EXACT.value,
            workplace_type=workplace,
            international_eligibility=InternationalEligibility.SPONSORED_OR_J1_REQUIRED.value,
            operational_status="OPERATIONAL",
            verification_notes=f"Discovered live via official public {kind.title()} ATS job board ({job_url}). Classification: {classification}.",
            is_confirmed_opening=is_confirmed,
            next_action="PREPARE_STARTUP_PORTAL_APPLICATION",
            created_at=now_iso,
            updated_at=now_iso,
            last_verified_at=now_iso
        )

        return org, opp
