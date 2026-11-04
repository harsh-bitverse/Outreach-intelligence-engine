"""
Opportunity Intelligence Engine: Domain-Adaptive AI Personalization Engine.

Central Principle: Same verified applicant profile (IIT Delhi Chemical Engineering, 6 verified systems projects),
different relevant evidence and contribution narrative for each opportunity category:

1. Academic Professors: Research overlap, methodology, scientific contributions, Summer 2027 collaboration.
2. Startups: Founder/engineering communication, core technical systems, what applicant can build/ship in 8-12 weeks, technical ownership.
3. Frontier Companies: Systems benchmarks, role requirements, production contribution, careers portal packet.
4. Institutional Programs: Program eligibility, competencies, formal documentation, fellowship dossier.
"""

from typing import Dict, Any, Optional
from src.opportunities.models import Opportunity, Organization, OpportunityType


class DomainAdaptivePersonalizer:
    """
    Generates domain-adaptive outreach specifications and drafts tailored to opportunity categories.
    """

    APPLICANT_PROFILE = {
        "institution": "Indian Institute of Technology Delhi (IIT Delhi)",
        "major": "Chemical Engineering",
        "verified_projects": {
            "Neural Process Twin": {
                "headline": "Physics-Informed Neural Operator & SciML Surrogate Modeling",
                "stack": "PyTorch, SciML, PINNs, Finite-Difference Numerical Solvers",
                "metrics": "Trained neural operators for 2D steady-state heat conduction with finite-difference error estimation."
            },
            "LLM Inference Intelligence Engine": {
                "headline": "Roofline Hardware Diagnostics & Low-Latency ONNX Runtime Serving",
                "stack": "Roofline Model, ONNX Runtime, CUDA, Triton, Python, C++",
                "metrics": "Modeled compute bounds vs memory-bandwidth saturation; optimized token latency across heterogeneous hardware."
            },
            "Finite-Difference PINN Error Estimation": {
                "headline": "Numerical PDE Residual & Gradient Pathology Mitigation",
                "stack": "Finite Difference Methods, PyTorch, Numerical Analysis",
                "metrics": "Quantified PDE discretization error bounds versus neural network optimization errors."
            },
            "Peng-Robinson Real-Gas Cubic Equation of State Solver": {
                "headline": "Modern C++17 Numerical Thermodynamic Solver",
                "stack": "C++17, Numerical Root Finding, Chemical Thermodynamics",
                "metrics": "Implemented robust cubic equation of state solver for high-pressure non-ideal gas mixtures."
            },
            "Humanoid Robotics & SuRGE Microdrone": {
                "headline": "Autonomous Microdrone Flight & Embedded Kinematic Control",
                "stack": "C++, Embedded Systems, ROS2, Visual Odometry, Kinematics",
                "metrics": "Engineered real-time obstacle avoidance telemetry and multi-axis kinematic control loops."
            },
            "Sovereign On-Premise Agentic AI Workbench": {
                "headline": "Deterministic Multi-Step Autonomous Agent Orchestration",
                "stack": "Python, Tool Sandboxing, Graph Execution, Failure-Recovery Circuits",
                "metrics": "Built autonomous coding agent workbench with deterministic state checkpoints and tool sandboxing."
            }
        }
    }

    @classmethod
    def generate_startup_pitch(cls, opp: Opportunity, org: Organization) -> Dict[str, Any]:
        """
        Generates a concise, high-agency engineering contribution pitch to a startup founder or engineering lead.
        Focuses on what the applicant can build/optimize/ship during an 8-12 week Summer 2027 internship.
        """
        matched_proj_id = opp.matched_project_id or "LLM Inference Intelligence Engine"
        proj_info = cls.APPLICANT_PROFILE["verified_projects"].get(
            matched_proj_id,
            cls.APPLICANT_PROFILE["verified_projects"]["LLM Inference Intelligence Engine"]
        )

        recipient_name = opp.contact_name or "Founding Team"
        first_name = recipient_name.split()[0] if " " in recipient_name else recipient_name

        company_name = org.name

        # Construct specific subject line
        subject = f"Engineering Summer 2027: Systems contribution for {company_name} ({matched_proj_id})"

        # Paragraph 1: Direct technical hook into company's product direction
        p1 = (
            f"Hi {first_name},\n\n"
            f"I've been tracking {company_name}'s work on {opp.technical_relevance_summary or 'cutting-edge technical systems'}, "
            f"particularly the engineering demands of {', '.join(opp.technical_domains[:3]) if opp.technical_domains else 'low-latency infrastructure'}. "
            f"I'm an undergraduate at IIT Delhi (Chemical Engineering) with a focus on high-performance systems and scientific simulation, "
            f"writing to discuss a potential hands-on engineering contribution for Summer 2027 (8–12 weeks)."
        )

        # Paragraph 2: Verified engineering credentials & concrete proposed contribution
        p2 = (
            f"Recently, I independently developed `{matched_proj_id}`—{proj_info['headline']} ({proj_info['stack']}). "
            f"{proj_info['metrics']} "
            f"For {company_name}, I could immediately help with {opp.potential_contribution or 'optimizing core engine latency and building deterministic telemetry pipelines'}. "
            f"I care about shipping production code, profiling memory bounds, and taking complete ownership of core technical bottlenecks rather than peripheral tasks."
        )

        # Paragraph 3: Availability & call to action
        p3 = (
            f"I am available for full-time work between May and August 2027. "
            f"I would welcome 15 minutes to share my project codebases and discuss where my systems background can be most useful to your team this summer.\n\n"
            f"Best regards,\n"
            f"Harsh Vardhan\n"
            f"Undergraduate, Indian Institute of Technology Delhi\n"
            f"Portfolio: github.com/harsh-bitverse"
        )

        body = f"{p1}\n\n{p2}\n\n{p3}"

        return {
            "category": OpportunityType.STARTUP.value,
            "organization_name": company_name,
            "recipient_name": recipient_name,
            "recipient_email": opp.contact_email,
            "matched_project": matched_proj_id,
            "project_stack": proj_info["stack"],
            "proposed_contribution": opp.potential_contribution,
            "pitch_subject": subject,
            "pitch_body": body,
            "tone": "CONCISE_FOUNDER_ENGINEERING_PITCH"
        }

    @classmethod
    def generate_portal_cover_statement(cls, opp: Opportunity, org: Organization) -> Dict[str, Any]:
        """
        Generates tailored technical cover notes for official careers portal submissions (Greenhouse/Lever/Ashby).
        """
        matched_proj_id = opp.matched_project_id or "Neural Process Twin"
        proj_info = cls.APPLICANT_PROFILE["verified_projects"].get(
            matched_proj_id,
            cls.APPLICANT_PROFILE["verified_projects"]["Neural Process Twin"]
        )

        statement = (
            f"I am applying for the {opp.title} at {org.name} for Summer 2027. "
            f"As an undergraduate at IIT Delhi with an engineering foundation in numerical methods and systems, "
            f"my technical experience directly intersects with {org.name}'s mission in {', '.join(opp.technical_domains or ['AI systems'])}.\n\n"
            f"In my project `{matched_proj_id}`, I developed {proj_info['headline']} using {proj_info['stack']}. "
            f"{proj_info['metrics']} "
            f"During this internship, I am looking to contribute to {opp.potential_contribution or 'production engineering systems'}. "
            f"I bring rigorous mathematical problem-solving from IIT Delhi, coupled with a track record of independently implementing, "
            f"debugging, and benchmarking end-to-end software systems."
        )

        return {
            "category": opp.opportunity_type,
            "organization_name": org.name,
            "role_title": opp.title,
            "portal_url": opp.application_url,
            "matched_project": matched_proj_id,
            "cover_statement": statement,
            "format": "OFFICIAL_CAREERS_PORTAL_APPLICATION"
        }
