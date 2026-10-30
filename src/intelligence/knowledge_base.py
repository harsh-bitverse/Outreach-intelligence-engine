"""
Personal Intelligence Base and Profile Manager.
Loads verified student identity, academic credentials, substantiated projects,
guidelines, and manages the editable research profile with missing source document auditing.
"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from config.settings import settings


class KnowledgeBase:
    """Provides access to verified student profile, projects, guidelines, and document audits."""

    def __init__(self, kb_dir: Optional[Path] = None):
        self.kb_dir = kb_dir or settings.KNOWLEDGE_BASE_DIR
        self.editable_profile_path = self.kb_dir / "editable_research_profile.json"
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

    # --------------------------------------------------------------------------
    # Editable Research Profile & Missing Sources Audit
    # --------------------------------------------------------------------------
    def load_editable_profile(self) -> Dict[str, Any]:
        """Load the comprehensive editable research profile."""
        if not self.editable_profile_path.exists():
            return {}
        with open(self.editable_profile_path, "r", encoding="utf-8") as f:
            profile = json.load(f)
        self._refresh_documents_audit(profile)
        return profile

    def save_editable_profile(self, new_profile_data: Dict[str, Any]) -> Dict[str, Any]:
        """Persist updated research profile."""
        self._refresh_documents_audit(new_profile_data)
        self.editable_profile_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.editable_profile_path, "w", encoding="utf-8") as f:
            json.dump(new_profile_data, f, indent=2)
        return new_profile_data

    load_profile = load_editable_profile
    save_profile = save_editable_profile

    def _refresh_documents_audit(self, profile: Dict[str, Any]) -> None:
        """Inspect actual local filesystem for source files and update status."""
        import hashlib
        docs = profile.get("source_documents_audit", {})
        for doc_key, doc_entry in docs.items():
            path_str = doc_entry.get("path")
            if not path_str:
                continue
            doc_file = Path(path_str)
            if not doc_file.is_absolute():
                doc_file = settings.BASE_DIR / doc_file

            if doc_file.exists():
                doc_entry["status"] = "VERIFIED_PRESENT"
                doc_entry["file_size_bytes"] = doc_file.stat().st_size
                try:
                    doc_entry["sha256"] = hashlib.sha256(doc_file.read_bytes()).hexdigest()
                except Exception:
                    pass
            else:
                doc_entry["status"] = "MISSING_FROM_DISK"
                doc_entry["file_size_bytes"] = 0

    def audit_missing_sources(self) -> Dict[str, Any]:
        """Return dedicated audit report of available vs missing source documents."""
        profile = self.load_editable_profile()
        docs = profile.get("source_documents_audit", {})
        missing = []
        present = []

        for doc_key, doc_info in docs.items():
            if doc_info.get("status") == "MISSING_FROM_DISK":
                missing.append({
                    "document_key": doc_key,
                    "expected_path": doc_info.get("path"),
                    "mandatory": doc_info.get("mandatory_before_dispatch", False),
                    "remediation": doc_info.get("remediation")
                })
            else:
                present.append({
                    "document_key": doc_key,
                    "description": doc_info.get("description", "Available")
                })

        return {
            "all_mandatory_sources_ready": len(missing) == 0,
            "missing_count": len(missing),
            "present_count": len(present),
            "missing_documents": missing,
            "present_documents": present
        }


# Global knowledge base singletons
knowledge_base = KnowledgeBase()
kb_manager = knowledge_base
