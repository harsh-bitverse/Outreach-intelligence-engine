"""
Immutable Audit Logging Module.
Maintains append-only audit trail for all operations, approvals, and transmissions.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from config.settings import settings


class AuditLogger:
    """Manages immutable audit logging for security and compliance."""

    def __init__(self, log_file: Optional[Path] = None):
        self.log_file = log_file or (settings.LOGS_DIR / "audit_events.jsonl")
        self.log_file.parent.mkdir(parents=True, exist_ok=True)

    def log_event(
        self,
        event_type: str,
        actor: str,
        entity_type: str,
        entity_id: str,
        details: Optional[Dict[str, Any]] = None,
        operator_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Record an immutable event entry to the audit log.
        """
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "actor": actor,
            "operator_id": operator_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "details": details or {}
        }

        # Append JSON line to log file atomically
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(line)

        return record


# Global audit logger instance
audit_logger = AuditLogger()
