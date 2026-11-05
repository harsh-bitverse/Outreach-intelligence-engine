"""
Opportunity Intelligence Engine: Domain-Adaptive AI Personalization Engine.

Central Principle: Same verified applicant profile (IIT Delhi Chemical Engineering, 6 verified systems projects),
different relevant evidence and contribution narrative for each opportunity category:

1. Academic Professors: Research overlap, methodology, scientific contributions, Summer 2027 collaboration.
2. Startups: Founder/engineering communication, core technical systems, what applicant can build/ship in 8-12 weeks, technical ownership.
3. Frontier Companies: Systems benchmarks, role requirements, production contribution, careers portal packet.
4. Institutional Programs: Program eligibility, competencies, formal documentation, fellowship dossier.
"""

from typing import Dict, Any, Optional, List
from src.opportunities.models import Opportunity, Organization, OpportunityType


class DomainAdaptivePersonalizer:
    """
    Generates domain-adaptive outreach specifications and drafts tailored to opportunity categories.
    """

    APPLICANT_PROFILE = {
        "full_name": "Harshith N",
        "institution": "Indian Institute of Technology Delhi (IIT Delhi)",
        "major": "Chemical Engineering",
        "portfolio_url": "github.com/harsh-bitverse",
        "verified_projects": {
            "Neural Process Twin": {
                "headline": "Physics-Informed Neural Operator & SciML Surrogate Modeling",
                "stack": "Python, PyTorch, SciML, PINNs, Finite-Difference Numerical Solvers",
                "metrics": "Trained neural operator surrogates for 2D steady-state transport equations; benchmarked convergence and surrogate fidelity against finite-difference numerical baselines."
            },
            "LLM Inference Intelligence Engine": {
                "headline": "Roofline Hardware Diagnostics & Low-Latency ONNX Runtime Serving",
                "stack": "Roofline Model, ONNX Runtime, Python, C++, Linux",
                "metrics": "Formulated Roofline operational intensity bounds vs memory-bandwidth saturation; benchmarked token latency trade-offs across CPU and GPU hardware."
            },
            "PINN Error Estimation term paper": {
                "headline": "Numerical PDE Residual & Gradient Pathology Mitigation",
                "stack": "Finite Difference Methods, PyTorch, Numerical Analysis, LaTeX",
                "metrics": "Authored term paper analyzing PDE residual discretization error bounds versus neural network optimization errors, studying gradient pathology in physics-constrained loss functions."
            },
            "C++ Peng–Robinson EOS solver": {
                "headline": "Modern C++17 Numerical Thermodynamic Real-Gas Solver",
                "stack": "C++17, Numerical Root Finding, Chemical Thermodynamics, CMake",
                "metrics": "Engineered robust C++17 cubic equation of state solver for high-pressure non-ideal gas/fluid mixtures with Cardano root solving and thermodynamic property routines."
            },
            "SuRGE microdrone work": {
                "headline": "Autonomous Microdrone Flight & Embedded Kinematic Control",
                "stack": "C++, Embedded Systems, ROS2, Visual Odometry, Telemetry",
                "metrics": "Implemented telemetry processing, visual odometry interfaces, and multi-axis kinematic control loops for autonomous microdrone obstacle navigation."
            },
            "Agentic Industrial AI Workbench": {
                "headline": "Deterministic Multi-Step Autonomous Agent Orchestration",
                "stack": "Python, Asyncio, Tool Sandboxing, Graph Execution, Failure-Recovery Circuits",
                "metrics": "Designed autonomous coding agent workbench featuring deterministic state checkpoints, bounded exponential backoff, and tool sandboxing."
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
            f"In my independent technical work at IIT Delhi, I developed `{matched_proj_id}`—{proj_info['headline']} ({proj_info['stack']}). "
            f"{proj_info['metrics']} "
            f"During an 8–12 week Summer 2027 internship, I propose to contribute directly to {company_name} by {opp.potential_contribution or 'benchmarking memory bounds and implementing deterministic testing harnesses for core services'}. "
            f"I focus on writing clean, reproducible systems code, understanding hardware bottlenecks, and taking rigorous ownership of technical tasks."
        )

        # Paragraph 3: Availability & call to action
        p3 = (
            f"I am fully available for 8–12 weeks full-time between May and August 2027 (in-person or remote as preferred). "
            f"My complete independent project codebases and technical documentation are available at {cls.APPLICANT_PROFILE['portfolio_url']}. "
            f"I would welcome a brief 15-minute conversation to share my work and discuss where my systems background can be most impactful to your team this summer.\n\n"
            f"Sincerely,\n"
            f"{cls.APPLICANT_PROFILE['full_name']}\n"
            f"Undergraduate, {cls.APPLICANT_PROFILE['major']}\n"
            f"{cls.APPLICANT_PROFILE['institution']}\n"
            f"Portfolio: {cls.APPLICANT_PROFILE['portfolio_url']}"
        )

        body = f"{p1}\n\n{p2}\n\n{p3}"

        return {
            "applicant_name": cls.APPLICANT_PROFILE["full_name"],
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
            f"Position: {opp.title} — {org.name}\n"
            f"Applicant: {cls.APPLICANT_PROFILE['full_name']}, Undergraduate in {cls.APPLICANT_PROFILE['major']}, {cls.APPLICANT_PROFILE['institution']}\n"
            f"Portfolio: {cls.APPLICANT_PROFILE['portfolio_url']}\n\n"
            f"I am writing to express my strong interest in joining {org.name} for Summer 2027 (8–12 weeks). "
            f"My academic foundation at IIT Delhi centers on numerical transport phenomena, coupled with independent systems and machine learning engineering.\n\n"
            f"Completed Technical Work:\n"
            f"In my project `{matched_proj_id}`, I built {proj_info['headline']} using {proj_info['stack']}. "
            f"{proj_info['metrics']}\n\n"
            f"Proposed Summer 2027 Contribution:\n"
            f"At {org.name}, I am eager to apply this rigorous foundation to {opp.potential_contribution or 'production systems engineering and benchmark verification'}. "
            f"I bring deep mathematical problem-solving, verifiable codebases on GitHub, and a proven track record of independently implementing and debugging complex software pipelines."
        )

        return {
            "applicant_name": cls.APPLICANT_PROFILE["full_name"],
            "category": opp.opportunity_type,
            "organization_name": org.name,
            "role_title": opp.title,
            "portal_url": opp.application_url,
            "matched_project": matched_proj_id,
            "cover_statement": statement,
            "format": "OFFICIAL_CAREERS_PORTAL_APPLICATION"
        }

    @classmethod
    def generate_industry_pitch(cls, opp: Opportunity, org: Organization) -> Dict[str, Any]:
        """
        Generates a systems-focused engineering pitch for frontier tech companies
        (e.g., Nvidia, Groq, Cerebras, Red Hat vLLM, ANSYS, Siemens, Tesla, Apple, AWS).
        Emphasizes low-latency execution, hardware bottlenecks, reproducible benchmarking,
        and concrete 8-12 week Summer 2027 deliverables.
        """
        matched_proj_id = opp.matched_project_id or "LLM Inference Intelligence Engine"
        proj_info = cls.APPLICANT_PROFILE["verified_projects"].get(
            matched_proj_id,
            cls.APPLICANT_PROFILE["verified_projects"]["LLM Inference Intelligence Engine"]
        )

        recipient_name = opp.contact_name or f"{org.name} Engineering Team"
        first_name = recipient_name.split()[0] if " " in recipient_name else recipient_name
        company_name = org.name

        subject = f"Summer 2027 Engineering Internship: Systems contribution for {company_name} ({matched_proj_id})"

        p1 = (
            f"Hi {first_name},\n\n"
            f"I am writing to express my strong interest in an engineering internship at {company_name} for Summer 2027 (8–12 weeks). "
            f"I have closely followed {company_name}'s advancements in {opp.technical_relevance_summary or 'scalable systems infrastructure'}, "
            f"specifically in {', '.join(opp.technical_domains[:3]) if opp.technical_domains else 'low-latency engineering'}. "
            f"As an undergraduate at IIT Delhi (Chemical Engineering) specializing in high-performance computing, numerical simulation, and ML systems, "
            f"I focus on writing clean, reproducible systems software that addresses concrete hardware bottlenecks."
        )

        p2 = (
            f"In my independent technical work at IIT Delhi, I built `{matched_proj_id}`—{proj_info['headline']} ({proj_info['stack']}). "
            f"{proj_info['metrics']} "
            f"During Summer 2027, I propose to contribute directly to {company_name} by {opp.potential_contribution or 'implementing deterministic performance benchmarks, profiling memory-bandwidth saturation, and optimizing runtime execution'}. "
            f"I understand the discipline required for production software: rigorous regression testing, latency-tail profiling, and zero-overhead abstractions."
        )

        p3 = (
            f"I am fully available for 8–12 weeks between May and August 2027. "
            f"My complete open-source projects, architecture documentation, and numerical codebases are accessible at {cls.APPLICANT_PROFILE['portfolio_url']}. "
            f"I would welcome the opportunity to discuss how my systems and numerical background can contribute directly to {company_name}'s engineering roadmap this summer.\n\n"
            f"Sincerely,\n"
            f"{cls.APPLICANT_PROFILE['full_name']}\n"
            f"Undergraduate, {cls.APPLICANT_PROFILE['major']}\n"
            f"{cls.APPLICANT_PROFILE['institution']}\n"
            f"Portfolio: {cls.APPLICANT_PROFILE['portfolio_url']}"
        )

        body = f"{p1}\n\n{p2}\n\n{p3}"

        return {
            "applicant_name": cls.APPLICANT_PROFILE["full_name"],
            "category": OpportunityType.FRONTIER_COMPANY.value,
            "organization_name": company_name,
            "role_title": opp.title,
            "recipient_name": recipient_name,
            "recipient_email": opp.contact_email,
            "matched_project": matched_proj_id,
            "project_stack": proj_info["stack"],
            "proposed_contribution": opp.potential_contribution,
            "pitch_subject": subject,
            "pitch_body": body,
            "tone": "PRODUCTION_ENGINEERING_CONTRIBUTION"
        }

    @classmethod
    def generate_industrial_research_pitch(cls, opp: Opportunity, org: Organization) -> Dict[str, Any]:
        """
        Generates an academically rigorous pitch for premier industrial research laboratories
        (e.g., Microsoft Research, Google DeepMind, Meta FAIR, Toyota Research Institute).
        Emphasizes mathematical formulation, empirical ablation, and scientific investigation.
        """
        matched_proj_id = opp.matched_project_id or "PINN Error Estimation term paper"
        proj_info = cls.APPLICANT_PROFILE["verified_projects"].get(
            matched_proj_id,
            cls.APPLICANT_PROFILE["verified_projects"]["PINN Error Estimation term paper"]
        )

        recipient_name = opp.contact_name or f"{org.name} Research Team"
        company_name = org.name

        subject = f"Summer 2027 Research Placement: Scientific ML & Systems Investigation at {company_name}"

        p1 = (
            f"Dear {recipient_name},\n\n"
            f"I am writing to inquire about research internship opportunities in your group at {company_name} for Summer 2027 (10–12 weeks). "
            f"I have studied your recent publications in {', '.join(opp.technical_domains[:3]) if opp.technical_domains else 'applied machine learning and computational science'}, "
            f"and I am deeply interested in your work on {opp.technical_relevance_summary or 'foundation models and physical simulation'}. "
            f"I am an undergraduate at IIT Delhi in Chemical Engineering with a strong foundation in transport phenomena, numerical PDE solvers, and machine learning."
        )

        p2 = (
            f"In my research work at IIT Delhi, I authored `{matched_proj_id}`—{proj_info['headline']} ({proj_info['stack']}). "
            f"{proj_info['metrics']} "
            f"At {company_name}, I am eager to collaborate on {opp.potential_contribution or 'investigating neural operator error bounds, loss landscape dynamics, and scalable surrogate modeling for physical systems'}. "
            f"I approach research with mathematical rigor, reproducible experimental protocols, and thorough empirical ablation studies."
        )

        p3 = (
            f"I am available for full-time research from May through August 2027. "
            f"My technical papers, project code, and mathematical solvers are documented at {cls.APPLICANT_PROFILE['portfolio_url']}. "
            f"I would appreciate the chance to discuss potential alignment with your group's upcoming summer research agenda.\n\n"
            f"Best regards,\n"
            f"{cls.APPLICANT_PROFILE['full_name']}\n"
            f"Undergraduate, {cls.APPLICANT_PROFILE['major']}\n"
            f"{cls.APPLICANT_PROFILE['institution']}\n"
            f"Portfolio: {cls.APPLICANT_PROFILE['portfolio_url']}"
        )

        body = f"{p1}\n\n{p2}\n\n{p3}"

        return {
            "applicant_name": cls.APPLICANT_PROFILE["full_name"],
            "category": "INDUSTRIAL_RESEARCH_LAB",
            "organization_name": company_name,
            "role_title": opp.title,
            "recipient_name": recipient_name,
            "recipient_email": opp.contact_email,
            "matched_project": matched_proj_id,
            "project_stack": proj_info["stack"],
            "proposed_contribution": opp.potential_contribution,
            "pitch_subject": subject,
            "pitch_body": body,
            "tone": "SCIENTIFIC_RESEARCH_COLLABORATION"
        }

    @classmethod
    def generate_institutional_application(cls, opp: Opportunity, org: Organization) -> Dict[str, Any]:
        """
        Generates a formal Statement of Purpose and application packet for public research
        institutions and international fellowships (e.g., CERN openlab, DAAD WISE, Mitacs, RIKEN, ISTA).
        Directly references eligibility, required documentation, and institutional research objectives.
        """
        matched_proj_id = opp.matched_project_id or "Neural Process Twin"
        proj_info = cls.APPLICANT_PROFILE["verified_projects"].get(
            matched_proj_id,
            cls.APPLICANT_PROFILE["verified_projects"]["Neural Process Twin"]
        )

        sop = (
            f"STATEMENT OF PURPOSE & RESEARCH PROPOSAL\n"
            f"Program: {opp.title}\n"
            f"Institution: {org.name}\n"
            f"Applicant: {cls.APPLICANT_PROFILE['full_name']}\n"
            f"Institution: {cls.APPLICANT_PROFILE['institution']} | Major: {cls.APPLICANT_PROFILE['major']}\n"
            f"Portfolio & Codebases: {cls.APPLICANT_PROFILE['portfolio_url']}\n\n"
            f"1. Academic Background & Scientific Motivation:\n"
            f"As an undergraduate in Chemical Engineering at IIT Delhi, my academic training emphasizes numerical analysis, "
            f"transport phenomena (fluid flow, heat transfer, mass transport), and thermodynamic state modeling. "
            f"Complementing my formal coursework, I have conducted intensive independent engineering in physics-informed machine learning, "
            f"C++ systems programming, and high-performance computing.\n\n"
            f"2. Relevant Technical Research & Verified Implementation:\n"
            f"In my research project `{matched_proj_id}` ({proj_info['headline']}), I utilized {proj_info['stack']}. "
            f"{proj_info['metrics']} "
            f"This work established my capability to independently derive mathematical governing equations, implement robust numerical discretization schemes, "
            f"and debug optimization instabilities in high-dimensional scientific models.\n\n"
            f"3. Proposed Fellowship Contribution & Research Alignment:\n"
            f"During the {opp.internship_duration_weeks}-week tenure of the {opp.title} at {org.name}, my objective is to contribute to {opp.potential_contribution or 'the host laboratory’s ongoing computational and scientific workflows'}. "
            f"Specifically, I intend to bridge my understanding of physical conservation laws with large-scale computational infrastructure in {', '.join(opp.technical_domains[:3]) if opp.technical_domains else 'computational science'}.\n\n"
            f"4. Institutional Commitment & Prepared Dossier:\n"
            f"I meet all eligibility criteria as an enrolled full-time engineering undergraduate. "
            f"My complete application dossier—including official IIT Delhi transcripts, academic curriculum vitae, faculty reference letters, and verified GitHub implementations—is fully prepared for review. "
            f"I am available to conduct onsite research for the full duration of Summer 2027."
        )

        return {
            "applicant_name": cls.APPLICANT_PROFILE["full_name"],
            "category": OpportunityType.INSTITUTIONAL_PROGRAM.value,
            "organization_name": org.name,
            "program_title": opp.title,
            "portal_url": opp.application_url,
            "matched_project": matched_proj_id,
            "duration_weeks": opp.internship_duration_weeks,
            "stipend_acknowledged": opp.stipend_amount or opp.compensation_details,
            "travel_support": opp.travel_support,
            "housing_support": opp.housing_support,
            "required_documents": opp.required_documents or "CV, transcripts, faculty references, statement of interest",
            "statement_of_purpose": sop,
            "format": "FELLOWSHIP_STATEMENT_OF_PURPOSE"
        }

    @classmethod
    def generate_ten_verified_startup_pitches(cls) -> List[Dict[str, Any]]:
        """
        Generates exactly ten factually grounded startup-specific pitch examples for distinct companies,
        strictly referencing the applicant's verified technical projects without exaggeration.
        """
        from src.opportunities.registry import compute_organization_id, compute_opportunity_id

        pitch_specs = [
            {
                "company": "Sarvam AI",
                "recipient": "Founding Engineering Team",
                "email": "careers@sarvam.ai",
                "domain": "Indic LLM Inference & Systems",
                "relevance": "low-resource tokenization and memory-constrained inference serving",
                "matched_proj": "LLM Inference Intelligence Engine",
                "problem": "Indian language tokenization leads to longer sequence lengths, saturating GPU memory bandwidth during auto-regressive generation",
                "proposed": "benchmarking Roofline operational intensity and implementing memory-efficient tokenization batching harnesses to reduce prefill latency"
            },
            {
                "company": "Together AI",
                "recipient": "Inference Systems Lead",
                "email": "talent@together.xyz",
                "domain": "High-Throughput Model Serving",
                "relevance": "distributed inference infrastructure and GPU serving optimization",
                "matched_proj": "LLM Inference Intelligence Engine",
                "problem": "multi-tenant serving creates severe KV-cache memory bandwidth contention under bursty request spikes",
                "proposed": "profiling memory-bandwidth saturation thresholds and integrating automated load-testing benchmarks for long-context inference pipelines"
            },
            {
                "company": "Fireworks AI",
                "recipient": "Lin Qiao / Engineering Team",
                "email": "careers@fireworks.ai",
                "domain": "Low-Latency LLM Inference Serving",
                "relevance": "fast inference engines and structured output decoding",
                "matched_proj": "LLM Inference Intelligence Engine",
                "problem": "structured JSON decoding introduces runtime overheads that degrade first-token latency",
                "proposed": "benchmarking ONNX Runtime and serving execution bottlenecks to help validate speculative decoding pipelines"
            },
            {
                "company": "PhysicsX",
                "recipient": "Engineering Team",
                "email": "careers@physicsx.ai",
                "domain": "Scientific ML & Multiphysics Simulation",
                "relevance": "physics-informed surrogate models for complex engineering flows",
                "matched_proj": "Neural Process Twin",
                "problem": "standard PINN formulations suffer gradient pathologies when enforcing stiff boundary conditions in advection-dominated transport",
                "proposed": "implementing finite-difference residual error metrics and adaptive loss weighting to improve neural operator surrogate stability"
            },
            {
                "company": "Baseten",
                "recipient": "Model Serving Infrastructure Lead",
                "email": "talent@baseten.co",
                "domain": "Model Serving & Performance Engineering",
                "relevance": "containerized open-weight model deployment and cold-start reduction",
                "matched_proj": "LLM Inference Intelligence Engine",
                "problem": "cold starts and heterogeneous hardware memory pooling create unpredictable latency tails",
                "proposed": "building automated performance profiling harnesses and benchmarking memory-pinned model weight loading routines"
            },
            {
                "company": "Cognition",
                "recipient": "Scott Wu / Engineering Team",
                "email": "careers@cognition.ai",
                "domain": "Autonomous Coding Agent Architecture",
                "relevance": "agent tool execution sandboxes and deterministic recovery loops",
                "matched_proj": "Agentic Industrial AI Workbench",
                "problem": "long-horizon code generation tasks encounter cascading tool execution errors and nondeterministic state corruption",
                "proposed": "contributing to deterministic execution checkpointing, failure-recovery circuits, and isolated subprocess sandboxing"
            },
            {
                "company": "CynLr",
                "recipient": "Robotics Vision Team",
                "email": "careers@cynlr.com",
                "domain": "Robot Vision & Visual Servoing",
                "relevance": "depth-agnostic robotic manipulation and real-time kinematic control",
                "matched_proj": "SuRGE microdrone work",
                "problem": "dynamic visual feedback loops require microsecond sensor telemetry integration to prevent control instability",
                "proposed": "designing robust C++ telemetry pipelines and visual odometry integration routines for real-time robotic servo loops"
            },
            {
                "company": "Modular",
                "recipient": "Chris Lattner / Systems Team",
                "email": "careers@modular.com",
                "domain": "High-Performance Compiler & Tensor Runtime",
                "relevance": "hardware-accelerated tensor compilation and systems programming",
                "matched_proj": "C++ Peng–Robinson EOS solver",
                "problem": "numerical thermodynamic solvers and physical kernels require zero-cost abstractions and deterministic memory management",
                "proposed": "benchmarking numerical solver routines and porting thermodynamic equation-of-state kernels to test MAX engine runtime performance"
            },
            {
                "company": "Agnikul Cosmos",
                "recipient": "Propulsion & Simulation Team",
                "email": "careers@agnikul.in",
                "domain": "Cryogenic Propulsion & Flight Simulation",
                "relevance": "thermodynamic fluid modeling and real-gas combustor simulation",
                "matched_proj": "C++ Peng–Robinson EOS solver",
                "problem": "cryogenic propellants operate near supercritical conditions where ideal-gas assumptions fail, requiring fast cubic equation of state evaluations",
                "proposed": "integrating robust C++17 cubic equation-of-state flash routines and numerical root solvers into combustion chamber simulation pipelines"
            },
            {
                "company": "Modal Labs",
                "recipient": "Erik Bernhardsson / Systems Team",
                "email": "jobs@modal.com",
                "domain": "Serverless Cloud Runtime for AI Systems",
                "relevance": "fast container scheduling, memory snapshotting, and distributed Python orchestration",
                "matched_proj": "Agentic Industrial AI Workbench",
                "problem": "distributed Python task orchestration requires robust retry semantics, bounded backoff, and rapid fault isolation under high concurrency",
                "proposed": "implementing automated resilience benchmarks and designing integration test harnesses for asynchronous distributed task queues"
            }
        ]

        pitches = []
        for spec in pitch_specs:
            org = Organization(
                organization_id=compute_organization_id(spec["company"], f"https://www.{spec['company'].lower().replace(' ', '')}.com"),
                name=spec["company"],
                category=OpportunityType.STARTUP.value
            )
            opp = Opportunity(
                opportunity_id=compute_opportunity_id(org.organization_id, OpportunityType.STARTUP.value, spec["domain"], "Summer 2027"),
                organization_id=org.organization_id,
                title=f"Summer 2027 Engineering Placement — {spec['domain']}",
                opportunity_type=OpportunityType.STARTUP.value,
                source="STARTUP_REGISTRY",
                technical_domains=[spec["domain"]],
                technical_relevance_summary=spec["relevance"],
                potential_contribution=spec["proposed"],
                matched_project_id=spec["matched_proj"],
                contact_email=spec["email"],
                contact_name=spec["recipient"]
            )
            pitch = cls.generate_startup_pitch(opp, org)
            pitch["company_problem_context"] = spec["problem"]
            pitches.append(pitch)

        return pitches
