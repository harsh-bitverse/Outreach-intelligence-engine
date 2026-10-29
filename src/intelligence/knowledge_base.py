"""
Personal Intelligence Base Loader.
Loads verified student identity, academic credentials, substantiated projects, and guidelines.
"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from config.settings import settings


class KnowledgeBase:
    """Provides access to verified student profile, projects, and guidelines."""

    def __init__(self, kb_dir: Optional[Path] = None):
        self.kb_dir = kb_dir or settings.KNOWLEDGE_BASE_DIR
        self._profile: Optional[Dict[str, Any]] = None
        self._projects: Optional[List[Dict[str, Any]]] = None
        self._guidelines: Optional[str] = None
        self.reload()

    def reload(self) -> None:
        """Reload all knowledge base files from disk."""
        profile_file = self.kb_dir / "student_profile.json"
        if profile_file.exists():
            with open(profile_file, "r", encoding="utf-8") as f:
                self._profile = json.load(f)

        projects_file = self.kb_dir / "verified_projects.json"
        if projects_file.exists():
            with open(projects_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self._projects = data.get("projects", [])

        guidelines_file = self.kb_dir / "outreach_guidelines.md"
        if guidelines_file.exists():
            with open(guidelines_file, "r", encoding="utf-8") as f:
                self._guidelines = f.read()

    @property
    def student_profile(self) -> Dict[str, Any]:
        return self._profile or {}

    @property
    def student_name(self) -> str:
        return self.student_profile.get("identity", {}).get("full_name", "Harshith N")

    @property
    def student_email(self) -> str:
        return self.student_profile.get("identity", {}).get("institutional_email", "ch1251635@iitd.ac.in")

    @property
    def student_department(self) -> str:
        return self.student_profile.get("identity", {}).get("department", "Department of Chemical Engineering")

    @property
    def student_institution(self) -> str:
        return self.student_profile.get("identity", {}).get("institution", "Indian Institute of Technology Delhi (IIT Delhi)")

    @property
    def projects(self) -> List[Dict[str, Any]]:
        return self._projects or []

    def get_best_matching_project(self, professor_research_text: str) -> Dict[str, Any]:
        """
        Identify the most relevant verified student project based on research domain keywords.
        """
        text_lower = professor_research_text.lower()
        best_project = None
        highest_score = -1

        for proj in self.projects:
            score = 0
            for domain in proj.get("suitable_for_professors_in", []):
                if domain.lower() in text_lower:
                    score += 2
            for tech in proj.get("technologies", []):
                if tech.lower() in text_lower:
                    score += 1
            if score > highest_score:
                highest_score = score
                best_project = proj

        # Fallback to Neural Process Twin as foundational SciML project if score is 0
        return best_project or (self.projects[0] if self.projects else {})


# Global knowledge base singleton
knowledge_base = KnowledgeBase()
