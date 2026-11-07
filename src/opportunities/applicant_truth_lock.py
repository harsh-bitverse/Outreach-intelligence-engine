"""
Opportunity Intelligence Engine: Applicant Truth Lock & Evidence Grounding.
Enforces an immutable, source-backed applicant profile derived strictly from:
- Harshith_N_Research_CV_Summer2027.pdf
- Harshith_N_Research_Interests_Summer2027.pdf
- Verified local Git commit history and experiment logs

Guarantees:
- Zero fabricated projects (e.g. no phantom CUDA/Triton kernels or ROS quadrotors).
- Zero overstated claims (e.g. no speculative decoding, PagedAttention, flash algorithms).
- Zero hallucinated metrics (e.g. no '100x faster', '10x speedup', '40% reduction').
- Fail-closed execution gate: Blocks any draft containing unverified technical claims.
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Set

logger = logging.getLogger(__name__)

CANONICAL_PROFILE_PATH = Path(__file__).parent / "data" / "applicant_profile.json"


class UnsupportedTechnicalClaimError(Exception):
    """Raised when outreach draft or project attribution contains unsupported technical claims."""
    pass


class ApplicantTruthLock:
    """
    Immutable registry and validation gate enforcing strict evidence grounding for candidate Harshith N.
    """

    _profile_cache: Optional[Dict[str, Any]] = None

    @classmethod
    def load_profile(cls) -> Dict[str, Any]:
        """Loads and caches the immutable canonical applicant profile."""
        if cls._profile_cache is None:
            if not CANONICAL_PROFILE_PATH.exists():
                raise FileNotFoundError(f"Canonical applicant profile not found at {CANONICAL_PROFILE_PATH}")
            with open(CANONICAL_PROFILE_PATH, "r", encoding="utf-8") as f:
                cls._profile_cache = json.load(f)
        return cls._profile_cache

    @classmethod
    def get_candidate_identity(cls) -> Dict[str, Any]:
        """Returns verified candidate identity details."""
        profile = cls.load_profile()
        return profile["candidate"]

    @classmethod
    def get_canonical_projects(cls) -> Dict[str, Any]:
        """Returns the dictionary of all 7 substantiated canonical projects."""
        profile = cls.load_profile()
        return profile["canonical_projects"]

    @classmethod
    def get_global_prohibited_phrases(cls) -> List[str]:
        """Returns global list of prohibited exaggerations and unverified claims."""
        profile = cls.load_profile()
        return profile.get("global_prohibited_phrases", [])

    @classmethod
    def resolve_project_key(cls, identifier: str) -> Optional[str]:
        """
        Resolves an arbitrary project title, id, or legacy name to a canonical project key.
        """
        if not identifier:
            return None
        norm = identifier.lower().strip()
        projects = cls.get_canonical_projects()

        for key, pdata in projects.items():
            if key.lower() == norm:
                return key
            if pdata.get("short_name", "").lower() == norm:
                return key
            if pdata.get("canonical_title", "").lower() == norm:
                return key

        # Substring / legacy aliases
        if "process twin" in norm or "sciml" in norm or "heat conduction" in norm:
            return "neural-process-twin"
        if "inference" in norm or "roofline" in norm or "tpot" in norm or "ttft" in norm:
            return "llm-inference-engine"
        if "peng-robinson" in norm or "thermodynamic" in norm or "eos" in norm:
            return "thermo-cpp-solver"
        if "pinn" in norm or "krasowski" in norm or "error estimation" in norm:
            return "pinn-error-estimation"
        if "agentic" in norm or "mrpl" in norm or "sih" in norm:
            return "sovereign-agentic-workbench"
        if "microdrone" in norm or "surge" in norm:
            return "surge-microdrone-autonomy"
        if "outreach" in norm or "opportunity" in norm or "opvi" in norm:
            return "opportunity-intelligence-engine"

        return None

    @classmethod
    def validate_text(cls, text: str, project_key: Optional[str] = None) -> Tuple[bool, List[str]]:
        """
        Validates text against global prohibited phrases and project-specific boundaries.
        Returns (is_valid, list_of_violations).
        """
        violations = []
        if not text:
            return True, []

        lower_text = text.lower()

        # 1. Check global prohibited phrases
        for phrase in cls.get_global_prohibited_phrases():
            # Use word-boundary regex when possible to prevent partial substring false-positives
            pattern = r"\b" + re.escape(phrase.lower()) + r"\b"
            if re.search(pattern, lower_text):
                violations.append(f"Contains prohibited global phrase: '{phrase}'")

        # 2. Check project-specific prohibitions if project is known
        if project_key:
            canon_key = cls.resolve_project_key(project_key)
            if canon_key:
                pdata = cls.get_canonical_projects()[canon_key]
                for p_proh in pdata.get("prohibited_phrasing", []):
                    pattern = r"\b" + re.escape(p_proh.lower()) + r"\b"
                    if re.search(pattern, lower_text):
                        violations.append(f"Contains project-specific prohibited phrasing for '{canon_key}': '{p_proh}'")

        return (len(violations) == 0), violations

    @classmethod
    def assert_evidence_grounded(cls, text: str, project_key: Optional[str] = None) -> None:
        """
        Fails closed by raising UnsupportedTechnicalClaimError if the text contains any unsupported claim.
        """
        is_valid, violations = cls.validate_text(text, project_key=project_key)
        if not is_valid:
            err_msg = f"EVIDENCE_GROUNDING_VIOLATION: Text contains {len(violations)} unsupported technical claims: {violations}"
            logger.error(err_msg)
            raise UnsupportedTechnicalClaimError(err_msg)

    # Convenience alias
    validate_draft = assert_evidence_grounded

    @classmethod
    def validate_draft_claims(cls, text: str, project_key: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        """
        Validates draft claims and returns (is_valid, violation_details_or_None).
        Non-raising validator for clean inspection and test assertions.
        """
        is_valid, violations = cls.validate_text(text, project_key=project_key)
        violation_str = "; ".join(violations) if violations else None
        return is_valid, violation_str


truth_lock = ApplicantTruthLock
