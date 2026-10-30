"""
Test official Antigravity CLI (agy) integration for personalized drafting.
"""

import json
import re
import subprocess
from pathlib import Path

AGY_PATH = Path(r"C:\Users\hagrh\AppData\Local\agy\bin\agy.exe")


def test_draft_via_agy(prof_data: dict) -> dict:
    prompt = f"""You are an assistant for Harshith N, an undergraduate in Chemical Engineering at IIT Delhi (ch1251635@iitd.ac.in).
Generate a genuinely personalized, academically rigorous research internship outreach email to this professor for Summer 2027.

PROFESSOR DETAILS:
- Name: {prof_data['full_name']}
- Title: {prof_data.get('title', 'Professor')}
- University: {prof_data['university']}
- Department: {prof_data['department']}
- Research Areas: {prof_data['research_areas']}
- Specific Research Overlap: {prof_data['research_overlap']}
- Potential Contribution: {prof_data.get('potential_contribution', '')}

APPLICANT'S VERIFIED BACKGROUND:
- Student: Harshith N, Department of Chemical Engineering, IIT Delhi.
- Verified Projects:
  1. Neural Process Twin: PyTorch surrogate models for 2D steady-state heat conduction, evaluating surrogate prediction accuracy against numerical finite difference solutions.
  2. LLM Inference Intelligence Engine: Roofline model diagnostics isolating compute bounds from memory-bandwidth bottlenecks across CPU and GPU architectures.
- Documents attached: Harshith_N_Research_CV_Summer2027.pdf, Harshith_N_Research_Interests_Summer2027.pdf.

STRICT CONSTRAINTS:
1. Subject line format: Summer 2027 Research Internship — [Specific Concise Research Area] | IIT Delhi
2. Paragraph 1: State identity, department at IIT Delhi, and clear interest in visiting research internship in Summer 2027.
3. Paragraph 2: Discuss specific overlap with professor's research topics.
4. Paragraph 3: Connect to applicant's verified project (Neural Process Twin or LLM Inference Engine Roofline diagnostics) and realistic computational contribution.
5. Paragraph 4: Availability (10–12 weeks in Summer 2027) and natural reference to attached CV and research interests statement.
6. Total body word count across paragraphs 1-4 must be between 130 and 180 words.
7. Zero hype, zero unverified speedup numbers, zero certified bound claims.

Output ONLY valid JSON with keys:
"subject", "salutation", "p1", "p2", "p3", "p4", "signature"
"""

    cmd = [
        str(AGY_PATH),
        "--print",
        prompt
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"agy CLI failed (code {proc.returncode}): {proc.stderr}")

    raw_output = proc.stdout.strip()

    # Extract JSON block if surrounded by markdown code fences
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_output, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        # Try finding outermost braces
        start = raw_output.find("{")
        end = raw_output.rfind("}")
        if start != -1 and end != -1:
            json_str = raw_output[start:end+1]
        else:
            json_str = raw_output

    data = json.loads(json_str)
    return data


if __name__ == "__main__":
    prof = {
        "full_name": "Brent Young",
        "title": "Professor",
        "university": "The University of Auckland",
        "department": "Department of Chemical and Materials Engineering",
        "research_areas": "Process systems engineering, dynamic process simulation, digital twins",
        "research_overlap": "Synergy between faculty work in process systems engineering and digital twins",
        "potential_contribution": "Developing high-throughput computational screening pipelines"
    }

    print("Invoking Antigravity CLI (agy.exe) for Brent Young...")
    draft = test_draft_via_agy(prof)
    print("\nResult:")
    print("Subject:", draft.get("subject"))
    print("Salutation:", draft.get("salutation"))
    print("P1:", draft.get("p1"))
    print("P2:", draft.get("p2"))
    print("P3:", draft.get("p3"))
    print("P4:", draft.get("p4"))
    words = sum(len(p.split()) for p in [draft.get("p1", ""), draft.get("p2", ""), draft.get("p3", ""), draft.get("p4", "")])
    print("Word count (4 paras):", words)
