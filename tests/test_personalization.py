"""
Unit Tests for Personalization Engine, 4-Paragraph Structure, Word Count, and Signature Constraints.
"""

import pytest
from src.intelligence.personalization import PersonalizationEngine, PersonalizationQualityError
from src.intelligence.knowledge_base import knowledge_base


@pytest.fixture
def engine():
    return PersonalizationEngine(knowledge_base)


def test_four_paragraph_structure_and_signature(engine):
    """Verify that generated draft strictly has 4 paragraphs and exact student signature without LinkedIn/GitHub."""
    sample_prof = {
        "professor_id": "prof_test_mit_01",
        "full_name": "Abigail Bodner",
        "title": "Assistant Professor",
        "university_name": "Massachusetts Institute of Technology (MIT)",
        "email": "bodner@mit.edu",
        "research_areas": "Climate modeling, physical oceanography, scientific machine learning",
        "research_overlap": "Ocean dynamics, computational physics, surrogate modeling for physical fields",
        "cv_skill_project": "Neural Process Twin",
        "potential_contribution": "PyTorch surrogate modeling for climate PDE fields"
    }

    draft = engine.generate_draft(sample_prof)

    # 1. Subject verification
    assert "Summer 2027" in draft.subject
    assert "Harshith N" in draft.subject

    # 2. Signature verification
    body = draft.body_text
    assert "Sincerely,\nHarshith N" in body
    assert "Undergraduate, Department of Chemical Engineering" in body
    assert "Indian Institute of Technology Delhi (IIT Delhi)" in body
    assert "ch1251635@iitd.ac.in" in body

    # Strictly NO LinkedIn or GitHub links in signature/body
    assert "linkedin.com" not in body.lower()
    assert "github.com" not in body.lower()

    # 3. Four-paragraph content structure
    # Split by double newline: [Salutation, P1, P2, P3, P4, Signature]
    blocks = [b.strip() for b in body.split("\n\n") if b.strip()]
    assert len(blocks) >= 6

    salutation = blocks[0]
    p1 = blocks[1]
    p2 = blocks[2]
    p3 = blocks[3]
    p4 = blocks[4]

    assert salutation.startswith("Dear")
    assert "Harshith N" in p1 and "Summer 2027" in p1
    assert "group's focus on" in p2
    assert "Neural Process Twin" in p3 or "surrogate" in p3
    assert "10–12 weeks in Summer 2027" in p4

    # 4. Word count check (130-180 words)
    assert 130 <= draft.word_count <= 180, f"Word count {draft.word_count} is outside 130-180 words!"

    # 5. Validation function passes
    is_valid, err = engine.validate_draft(draft)
    assert is_valid is True
    assert err is None


def test_project_domain_routing(engine):
    """Verify appropriate verified project is selected based on faculty domain."""
    # Systems & LLM prof
    sys_prof = {
        "professor_id": "prof_sys_01",
        "full_name": "Matei Zaharia",
        "university_name": "UC Berkeley",
        "research_areas": "Machine learning systems, distributed computing, LLM inference serving",
        "research_overlap": "High-throughput inference, GPU architectures",
        "email": "matei@berkeley.edu"
    }
    draft_sys = engine.generate_draft(sys_prof)
    assert "LLM Inference Intelligence Engine" in draft_sys.body_text
    assert 130 <= draft_sys.word_count <= 180

    # Numerical analysis & PINN prof
    pinn_prof = {
        "professor_id": "prof_pinn_01",
        "full_name": "George Karniadakis",
        "university_name": "Brown University",
        "research_areas": "Applied mathematics, physics-informed neural networks, numerical PDE solvers",
        "research_overlap": "PINN error estimation, numerical analysis",
        "email": "george@brown.edu"
    }
    draft_pinn = engine.generate_draft(pinn_prof)
    assert "Physics-Informed Neural Networks" in draft_pinn.body_text or "numerical analysis" in draft_pinn.body_text
    assert 130 <= draft_pinn.word_count <= 180


def test_quality_gate_rejects_linkedin_or_github(engine):
    """Ensure quality gate immediately rejects any draft corrupted with social links."""
    sample_prof = {
        "professor_id": "prof_test",
        "full_name": "Test Prof",
        "university_name": "Test Uni",
        "email": "test@test.edu"
    }
    draft = engine.generate_draft(sample_prof)
    # Artificially inject linkedin link
    draft.body_text += "\nhttps://linkedin.com/in/test"
    is_valid, err = engine.validate_draft(draft)
    assert is_valid is False
    assert "LinkedIn/GitHub" in err
