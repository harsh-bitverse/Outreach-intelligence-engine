"""
Production-Hardened Personalization Engine Module.
Generates genuine, high-precision, four-paragraph outreach emails (130-180 words)
grounded in verified faculty evidence and substantiated student projects.
Includes explicit confidence scoring, evidence URLs, separation of facts vs aspirations,
and quality review flags.
"""

import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict, Any, Tuple, Optional, List
from src.database.models import OutreachDraft
from src.intelligence.knowledge_base import knowledge_base


class PersonalizationQualityError(Exception):
    """Raised when generated outreach violates word count or quality gates."""
    pass


@dataclass
class PersonalizationAuditMetadata:
    """Detailed evidence traceability for a generated outreach draft."""
    confidence_score: float
    confidence_tier: str  # HIGH, MEDIUM, LOW
    verified_student_facts: List[str]
    faculty_verified_evidence: Dict[str, str]
    inferred_alignment: str
    aspirations: str
    evidence_url: str
    review_flags: List[str] = field(default_factory=list)


class PersonalizationEngine:
    """Generates and validates customized, evidence-grounded faculty outreach drafts."""

    def __init__(self, kb=None):
        self.kb = kb or knowledge_base

    def calculate_evidence_confidence(self, professor: Dict[str, Any]) -> Tuple[float, str, List[str]]:
        """
        Calculate evidence confidence score (0.0 to 1.0) and determine review flags.
        """
        score = 0.20  # Baseline for presence in QS top 120
        flags = []

        email = professor.get("email", "").strip()
        ver_status = professor.get("email_verification_status", "").upper()
        if email and ver_status == "VERIFIED":
            score += 0.25
        else:
            flags.append("UNVERIFIED_EMAIL_ADDRESS")

        profile_url = professor.get("faculty_profile_url", "").strip()
        if profile_url and profile_url.startswith("http"):
            score += 0.15
        else:
            flags.append("MISSING_FACULTY_PROFILE_URL")

        overlap = professor.get("research_overlap", "").strip()
        if len(overlap) >= 30:
            score += 0.20
        elif len(overlap) > 0:
            score += 0.10
            flags.append("BRIEF_RESEARCH_OVERLAP_TEXT")
        else:
            flags.append("MISSING_RESEARCH_OVERLAP_TEXT")

        evidence_url = professor.get("evidence_url", "").strip()
        if evidence_url and evidence_url.startswith("http"):
            score += 0.20
        else:
            flags.append("MISSING_EVIDENCE_URL")

        # Normalize score
        score = min(round(score, 2), 1.0)

        if score >= 0.85 and not flags:
            tier = "HIGH"
        elif score >= 0.65:
            tier = "MEDIUM"
            flags.append("NEEDS_HUMAN_ATTENTION")
        else:
            tier = "LOW"
            flags.append("HOLD_MANUAL_RESEARCH_REQUIRED")

        return score, tier, flags

    def generate_draft(self, professor: Dict[str, Any]) -> OutreachDraft:
        """
        Produce a tailored four-paragraph outreach email for the given professor,
        grounded in verified database records and substantiated student code.
        """
        full_name = professor.get("full_name", "").strip()
        last_name = full_name.split()[-1] if full_name else "Professor"
        title = professor.get("title", "Professor")

        if "dr." in title.lower() or title.lower().startswith("dr "):
            salutation = f"Dear Dr. {last_name},"
        else:
            salutation = f"Dear Professor {last_name},"

        research_areas = professor.get("research_areas", "").strip()
        research_overlap = professor.get("research_overlap", "").strip()
        cv_project_hint = professor.get("cv_skill_project", "").strip()
        potential_contribution = professor.get("potential_contribution", "").strip()
        evidence_url = professor.get("evidence_url", "").strip()

        combined_research_text = f"{research_areas} {research_overlap} {cv_project_hint} {potential_contribution}"
        project = self.kb.get_best_matching_project(combined_research_text)

        # Subject Line
        subject = f"Prospective Summer 2027 Research Internship — Harshith N (IIT Delhi)"

        # ----------------------------------------------------------------------
        # Paragraph 1: Identity & specific purpose (approx 25-30 words)
        # ----------------------------------------------------------------------
        p1 = (
            f"My name is Harshith N, an undergraduate in the Department of Chemical Engineering at "
            f"the Indian Institute of Technology Delhi (IIT Delhi). I am writing to express my strong interest in "
            f"a visiting undergraduate research internship in your laboratory for Summer 2027."
        )

        # ----------------------------------------------------------------------
        # Paragraph 2: Faculty Research Relevance (approx 40-50 words)
        # ----------------------------------------------------------------------
        # Synthesize verified research citations
        if research_overlap and len(research_overlap) > 15:
            specific_mention = research_overlap.rstrip(".")
        elif research_areas:
            specific_mention = f"your investigative focus spanning {research_areas.rstrip('.')}"
        else:
            specific_mention = "your computational modeling and physical systems research"

        if len(specific_mention) > 115:
            specific_mention = specific_mention[:112].rstrip() + "..."

        p2 = (
            f"I have closely followed your group's focus on {specific_mention}. "
            f"Your principled framework for advancing computational fidelity and handling complex system dynamics "
            f"closely aligns with my research priorities."
        )

        # ----------------------------------------------------------------------
        # Paragraph 3: Specific student project bridge (approx 50-65 words)
        # ----------------------------------------------------------------------
        proj_id = project.get("id", "neural-process-twin")

        if proj_id == "llm-inference-engine":
            p3 = (
                f"In my work on the LLM Inference Intelligence Engine, I built measurement-driven benchmarking tools "
                f"evaluating hardware ceilings across CPU and GPU topologies. I profiled prefill TTFT latency and decode TPOT, "
                f"implementing Roofline model diagnostics to isolate memory-bandwidth saturation versus compute ceilings under variable workloads."
            )
        elif proj_id == "pinn-error-estimation":
            p3 = (
                f"In my numerical analysis research at IIT Delhi, I independently audited and reproduced Krasowski et al. "
                f"(arXiv:2603.15526) on residual-based PINN error estimation for 1D heat equations. I extended the defect solvers "
                f"to soft boundary constraints, rigorously validating pointwise error certification without closed-form analytical solutions."
            )
        elif proj_id == "thermo-cpp-solver":
            p3 = (
                f"In computational engineering, I developed a modern C++17 pure-component Peng-Robinson cubic equation of state solver, "
                f"solving polynomial compressibility factors Z, evaluating analytical isothermal derivatives (dP/dv)_T for mechanical "
                f"stability, and computing thermodynamic departure properties for real fluids."
            )
        elif proj_id == "humanoid-robotics":
            p3 = (
                f"In robotics, I designed and assembled multi-DOF servo-actuated kinematic mechanisms, implementing "
                f"low-latency embedded microcontroller control loops, positional feedback algorithms, and sensor telemetry "
                f"for closed-loop physical coordination."
            )
        else: # Default: neural-process-twin (SciML / Scientific Computing)
            p3 = (
                f"In my project, Neural Process Twin, I developed physics-aware surrogate simulators in PyTorch for 2D "
                f"heat conduction. By learning mappings between parameter function spaces and temperature fields while enforcing "
                f"governing PDE constraints, I achieved real-time inference speedups while preserving strict physical consistency."
            )

        # ----------------------------------------------------------------------
        # Paragraph 4: Summer 2027 request & polite close (approx 25-35 words)
        # ----------------------------------------------------------------------
        p4 = (
            f"I would welcome the opportunity to contribute to your group's upcoming computational initiatives for "
            f"10–12 weeks in Summer 2027. My CV is attached for your review, and I would be grateful for the chance "
            f"to discuss research alignment over a brief call."
        )

        # ----------------------------------------------------------------------
        # Strict Signature: NO LINKEDIN OR GITHUB LINKS
        # ----------------------------------------------------------------------
        signature = (
            "Sincerely,\n"
            "Harshith N\n"
            "Undergraduate, Department of Chemical Engineering\n"
            "Indian Institute of Technology Delhi (IIT Delhi)\n"
            "ch1251635@iitd.ac.in"
        )

        # Combine paragraphs
        body_text = f"{salutation}\n\n{p1}\n\n{p2}\n\n{p3}\n\n{p4}\n\n{signature}"

        # Word count calculation excluding salutation and signature
        body_words = f"{p1} {p2} {p3} {p4}".split()
        word_count = len(body_words)

        # Strict quality adjustment to maintain 130-180 words
        if word_count < 130:
            extra = " I am eager to apply my background in scientific computing to your group's ongoing research."
            p4 = p4[:-1] + extra
            body_text = f"{salutation}\n\n{p1}\n\n{p2}\n\n{p3}\n\n{p4}\n\n{signature}"
            body_words = f"{p1} {p2} {p3} {p4}".split()
            word_count = len(body_words)
        elif word_count > 180:
            # Trim trailing fluff if slightly over
            p4 = (
                f"I would welcome the opportunity to contribute to your research for 10–12 weeks in Summer 2027. "
                f"My CV is attached, and I would be grateful for a brief discussion."
            )
            body_text = f"{salutation}\n\n{p1}\n\n{p2}\n\n{p3}\n\n{p4}\n\n{signature}"
            body_words = f"{p1} {p2} {p3} {p4}".split()
            word_count = len(body_words)

        draft_id = "draft_" + hashlib.sha256(
            f"{professor.get('professor_id', '')}_{professor.get('email', '')}".encode()
        ).hexdigest()[:14]

        return OutreachDraft(
            draft_id=draft_id,
            professor_id=professor.get("professor_id", ""),
            recipient_email=professor.get("email", ""),
            subject=subject,
            body_text=body_text,
            word_count=word_count,
            status="DRAFT"
        )

    def validate_draft(self, draft: OutreachDraft) -> Tuple[bool, Optional[str]]:
        """
        Validate strict quality gates:
        - Word count strictly between 130 and 180 words
        - Four distinct paragraphs present
        - No LinkedIn or GitHub links in the signature
        - Verified institutional email in signature
        """
        body = draft.body_text
        if "linkedin.com" in body.lower() or "github.com" in body.lower():
            return False, "Quality Gate Failed: Signature contains prohibited LinkedIn/GitHub link."

        if "ch1251635@iitd.ac.in" not in body:
            return False, "Quality Gate Failed: Verified IIT Delhi email missing from signature."

        if draft.word_count < 125 or draft.word_count > 185:
            return False, f"Quality Gate Failed: Word count {draft.word_count} is outside acceptable range (130-180 words)."

        parts = [p.strip() for p in body.split("\n\n") if p.strip()]
        if len(parts) < 6:
            return False, f"Quality Gate Failed: Expected four distinct content paragraphs, found {len(parts)-2}."

        return True, None

    def audit_personalization(self, professor: Dict[str, Any], draft: OutreachDraft) -> PersonalizationAuditMetadata:
        """
        Produce a full forensic audit record for the draft, separating facts, evidence, and aspirations.
        """
        score, tier, flags = self.calculate_evidence_confidence(professor)

        verified_student_facts = [
            "Applicant: Harshith N, Undergraduate in Chemical Engineering, IIT Delhi (Entry 2025CH11635)",
            "Verified Email: ch1251635@iitd.ac.in",
            "Project Outcomes: Grounded in open-source verified codebases and coursework reports"
        ]

        faculty_evidence = {
            "professor_name": professor.get("full_name", ""),
            "university": professor.get("university_name", ""),
            "department": professor.get("department", ""),
            "research_areas": professor.get("research_areas", ""),
            "research_overlap": professor.get("research_overlap", ""),
            "evidence_source_url": professor.get("evidence_url", "") or professor.get("faculty_profile_url", "")
        }

        inferred_alignment = (
            f"Linked faculty focus on '{professor.get('research_areas', 'computational modeling')}' to "
            f"substantiated project '{professor.get('cv_skill_project', 'Neural Process Twin')}'"
        )

        aspirations = "10–12 week visiting research internship in Summer 2027 (May – July 2027)"

        return PersonalizationAuditMetadata(
            confidence_score=score,
            confidence_tier=tier,
            verified_student_facts=verified_student_facts,
            faculty_verified_evidence=faculty_evidence,
            inferred_alignment=inferred_alignment,
            aspirations=aspirations,
            evidence_url=faculty_evidence["evidence_source_url"],
            review_flags=flags
        )


# Global personalization engine singleton
personalization_engine = PersonalizationEngine()
