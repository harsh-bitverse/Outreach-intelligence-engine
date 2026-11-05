"""
Opportunity Intelligence Engine: Domain Models and Enums.
Provides type-safe data structures and validation for all opportunity categories.
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone


class OpportunityType(str, Enum):
    ACADEMIC_LAB = "ACADEMIC_LAB"
    STARTUP = "STARTUP"
    FRONTIER_COMPANY = "FRONTIER_COMPANY"
    INSTITUTIONAL_PROGRAM = "INSTITUTIONAL_PROGRAM"


class CompensationType(str, Enum):
    PAID = "PAID"
    STIPEND = "STIPEND"
    TRAVEL_LIVING_SUPPORT = "TRAVEL_LIVING_SUPPORT"
    UNPAID_ACADEMIC = "UNPAID_ACADEMIC"
    UNKNOWN = "UNKNOWN"


class ContactChannel(str, Enum):
    EMAIL = "EMAIL"
    APPLICATION_PORTAL = "APPLICATION_PORTAL"
    RECRUITER = "RECRUITER"
    FACULTY_PI = "FACULTY_PI"
    UNSPECIFIED = "UNSPECIFIED"


class VerificationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    NEEDS_CONTACT_VERIFICATION = "NEEDS_CONTACT_VERIFICATION"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class DiscoveryStatus(str, Enum):
    DISCOVERED = "DISCOVERED"
    QUALIFIED = "QUALIFIED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    DISQUALIFIED = "DISQUALIFIED"


class PriorityTier(str, Enum):
    HIGH_PRIORITY = "HIGH_PRIORITY"
    PROMISING = "PROMISING"
    NEEDS_VERIFICATION = "NEEDS_VERIFICATION"
    INELIGIBLE = "INELIGIBLE"
    REJECTED = "REJECTED"


class OpportunityClassification(str, Enum):
    CONFIRMED_OPENING = "CONFIRMED_OPENING"
    PROSPECTIVE_OUTREACH_TARGET = "PROSPECTIVE_OUTREACH_TARGET"
    FUTURE_OPENING_MONITOR = "FUTURE_OPENING_MONITOR"
    NEEDS_VERIFICATION = "NEEDS_VERIFICATION"
    INELIGIBLE = "INELIGIBLE"
    CLOSED = "CLOSED"


class RoleTitleType(str, Enum):
    EXACT = "EXACT"
    INFERRED = "INFERRED"
    PROPOSED_OUTREACH_FOCUS = "PROPOSED_OUTREACH_FOCUS"


class WorkplaceType(str, Enum):
    ONSITE = "ONSITE"
    HYBRID = "HYBRID"
    REMOTE = "REMOTE"
    UNKNOWN = "UNKNOWN"


class InternationalEligibility(str, Enum):
    ELIGIBLE_DOMESTIC = "ELIGIBLE_DOMESTIC"
    SPONSORED_OR_J1_REQUIRED = "SPONSORED_OR_J1_REQUIRED"
    RESTRICTED_DOMESTIC_ONLY = "RESTRICTED_DOMESTIC_ONLY"
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"


class RequisitionType(str, Enum):
    UNDERGRADUATE_INTERNSHIP = "UNDERGRADUATE_INTERNSHIP"
    GENERAL_ENGINEERING_VACANCY = "GENERAL_ENGINEERING_VACANCY"
    PROSPECTIVE_PITCH_TARGET = "PROSPECTIVE_PITCH_TARGET"
    FELLOWSHIP_PROGRAM = "FELLOWSHIP_PROGRAM"
    NEW_GRAD_FULL_TIME = "NEW_GRAD_FULL_TIME"
    PHD_RESEARCH = "PHD_RESEARCH"


class PortfolioTier(str, Enum):
    TIER_A_ELITE_PRIORITY = "TIER_A_ELITE_PRIORITY"
    TIER_B_STRONG_MATCH = "TIER_B_STRONG_MATCH"
    TIER_C_RESEARCH_PIPELINE = "TIER_C_RESEARCH_PIPELINE"


class ApplicationStatus(str, Enum):
    NOT_APPLIED = "NOT_APPLIED"
    DRAFTING = "DRAFTING"
    READY_TO_SUBMIT = "READY_TO_SUBMIT"
    SUBMITTED = "SUBMITTED"
    INTERVIEWING = "INTERVIEWING"
    OFFERED = "OFFERED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


class ContactHistoryStatus(str, Enum):
    DISCOVERED = "DISCOVERED"
    QUALIFIED = "QUALIFIED"
    VERIFIED_CONTACT = "VERIFIED_CONTACT"
    SCHEDULED_ELIGIBLE = "SCHEDULED_ELIGIBLE"
    ALREADY_CONTACTED = "ALREADY_CONTACTED"
    DELIVERY_UNCONFIRMED = "DELIVERY_UNCONFIRMED"
    BOUNCED = "BOUNCED"
    REPLIED = "REPLIED"
    PORTAL_APPLICATION = "PORTAL_APPLICATION"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NEEDS_CONTACT_VERIFICATION = "NEEDS_CONTACT_VERIFICATION"
    INVALID = "INVALID"
    UNVERIFIED = "UNVERIFIED"


class AcademicClassification(str, Enum):
    CONFIRMED_OPENING = "CONFIRMED_OPENING"
    PROSPECTIVE_INQUIRY = "PROSPECTIVE_INQUIRY"
    HOSTING_HISTORY_VERIFIED = "HOSTING_HISTORY_VERIFIED"
    INSTITUTION_POLICY_UNVERIFIED = "INSTITUTION_POLICY_UNVERIFIED"
    CONFIRMED_UNAVAILABLE_2027 = "CONFIRMED_UNAVAILABLE_2027"
    INELIGIBLE = "INELIGIBLE"


class VisitingStudentPolicy(str, Enum):
    FORMAL_VISITING_STUDENT_PROGRAM = "FORMAL_VISITING_STUDENT_PROGRAM"
    INDEPENDENT_RESEARCH_SCHOLAR_PERMITTED = "INDEPENDENT_RESEARCH_SCHOLAR_PERMITTED"
    RESTRICTED_TO_PARTNER_UNIVERSITIES = "RESTRICTED_TO_PARTNER_UNIVERSITIES"
    POLICY_UNVERIFIED = "POLICY_UNVERIFIED"
    NOT_PERMITTED = "NOT_PERMITTED"


class HostingAvailability(str, Enum):
    CONFIRMED_AVAILABLE = "CONFIRMED_AVAILABLE"
    CONFIRMED_UNAVAILABLE = "CONFIRMED_UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class FundingAvailability(str, Enum):
    CONFIRMED_FUNDED = "CONFIRMED_FUNDED"
    CONFIRMED_UNFUNDED = "CONFIRMED_UNFUNDED"
    EXTERNAL_SCHOLARSHIP_REQUIRED = "EXTERNAL_SCHOLARSHIP_REQUIRED"
    UNKNOWN = "UNKNOWN"


class AcademicReplyOutcome(str, Enum):
    INTERESTED = "INTERESTED"
    INTERVIEW_REQUESTED = "INTERVIEW_REQUESTED"
    CAPACITY_FULL = "CAPACITY_FULL"
    FUNDING_UNAVAILABLE = "FUNDING_UNAVAILABLE"
    APPLICATION_REFERRED = "APPLICATION_REFERRED"
    DECLINED = "DECLINED"
    UNRELATED_REPLY = "UNRELATED_REPLY"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NO_REPLY = "NO_REPLY"
    BOUNCED = "BOUNCED"


@dataclass
class Organization:
    organization_id: str
    name: str
    category: str
    website_url: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    iana_timezone: str = "UTC"
    description: Optional[str] = None
    funding_stage: Optional[str] = None
    tier: str = "STANDARD"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class QualificationBreakdown:
    technical_fit: float = 0.0        # max 35.0
    compensation: float = 0.0         # max 20.0
    ownership_impact: float = 0.0     # max 15.0
    eligibility: float = 0.0          # max 15.0
    learning_quality: float = 0.0     # max 10.0
    source_grounding: float = 0.0     # max 5.0
    total_score: float = 0.0          # max 100.0

    def calculate_total(self) -> float:
        self.total_score = round(
            min(100.0, max(0.0, (
                self.technical_fit +
                self.compensation +
                self.ownership_impact +
                self.eligibility +
                self.learning_quality +
                self.source_grounding
            ))), 1
        )
        return self.total_score

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Opportunity:
    opportunity_id: str
    organization_id: str
    title: str
    opportunity_type: str
    source: str = "CATALOG_CURATED"
    location: Optional[str] = None
    iana_timezone: str = "UTC"
    technical_domains: List[str] = field(default_factory=list)
    technical_relevance_summary: Optional[str] = None
    potential_contribution: Optional[str] = None
    matched_project_id: Optional[str] = None
    eligibility_criteria: Optional[str] = None
    internship_duration_weeks: int = 10
    target_term: str = "Summer 2027"
    compensation_type: str = CompensationType.UNKNOWN.value
    compensation_details: Optional[str] = None
    official_evidence_urls: List[str] = field(default_factory=list)
    freshness_timestamp: Optional[str] = None
    hiring_contact_channel: str = ContactChannel.UNSPECIFIED.value
    contact_email: Optional[str] = None
    contact_name: Optional[str] = None
    contact_verification_status: str = VerificationStatus.UNVERIFIED.value
    application_url: Optional[str] = None
    application_deadline: Optional[str] = None
    qualification_score: float = 0.0
    score_breakdown: Optional[QualificationBreakdown] = None
    priority_tier: Optional[str] = PriorityTier.NEEDS_VERIFICATION.value
    portfolio_tier: str = PortfolioTier.TIER_B_STRONG_MATCH.value
    audit_classification: str = OpportunityClassification.NEEDS_VERIFICATION.value
    requisition_type: str = RequisitionType.PROSPECTIVE_PITCH_TARGET.value
    role_title_type: str = RoleTitleType.PROPOSED_OUTREACH_FOCUS.value
    workplace_type: str = WorkplaceType.UNKNOWN.value
    international_eligibility: str = InternationalEligibility.NEEDS_CONFIRMATION.value
    compensation_evidence: Optional[str] = "UNPUBLISHED_BY_EMPLOYER"
    stipend_amount: Optional[str] = None
    travel_support: Optional[str] = None
    housing_support: Optional[str] = None
    required_documents: Optional[str] = None
    # Academic qualification, feasibility & reply integration dimensions
    academic_classification: Optional[str] = None
    institution_visiting_policy: str = VisitingStudentPolicy.POLICY_UNVERIFIED.value
    hosting_history_verified: bool = False
    hosting_history_evidence: Optional[str] = None
    summer_2027_hosting_availability: str = HostingAvailability.UNKNOWN.value
    funding_availability: str = FundingAvailability.UNKNOWN.value
    visa_sponsorship_status: str = "UNKNOWN"
    term_compatibility: bool = True
    application_route: str = "DIRECT_FACULTY_PI"
    evidence_sources: List[str] = field(default_factory=list)
    evidence_timestamp: Optional[str] = None
    verification_confidence: str = "MEDIUM"
    academic_reply_outcome: Optional[str] = None
    reply_timestamp: Optional[str] = None
    reply_evidence_snippet: Optional[str] = None
    reply_evidence_id: Optional[str] = None
    professor_id: Optional[str] = None
    contact_history_status: Optional[str] = None
    assigned_block_id: Optional[str] = None
    recipient_mailing_window: Optional[str] = None
    ist_eligibility_window: Optional[str] = None
    operational_status: str = "OPERATIONAL"
    verification_notes: Optional[str] = None
    last_verified_at: Optional[str] = None
    discovery_status: str = DiscoveryStatus.DISCOVERED.value
    application_status: str = ApplicationStatus.NOT_APPLIED.value
    is_confirmed_opening: bool = True
    next_action: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_checked_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.score_breakdown:
            d["score_breakdown"] = self.score_breakdown.to_dict()
        return d


@dataclass
class DiscoveryCheckpoint:
    source_key: str
    category: str
    last_queried_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    cursor_or_token: Optional[str] = None
    items_discovered_count: int = 0
    status: str = "IDLE"
    error_message: Optional[str] = None
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DiscoveryRun:
    run_id: str
    adapter_name: str
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None
    opportunities_found: int = 0
    opportunities_qualified: int = 0
    errors: List[str] = field(default_factory=list)
    status: str = "RUNNING"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
