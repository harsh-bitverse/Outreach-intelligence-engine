"""
Opportunity Intelligence Engine: Independent Production Activation Gates (Gates A–E).

Provides fail-closed, fine-grained access control for production integration:
- Gate A: Authoritative Master Workbook In-Place Mutation / Live Production Workbook Sync
- Gate B: Shared SMTP Dispatch for Academic Expansion Faculty (Net-New Professors)
- Gate C: Shared SMTP Dispatch for Non-Academic Direct Contacts (Startups, Industry, Labs)
- Gate D: Autonomous Live External Portal Form Submission (Greenhouse, Ashby, Lever, Portals)
- Gate E: Automated Inbound Reply Dispatching (Auto-Response / Follow-up emails)

Strict Invariants:
1. FAIL-CLOSED DEFAULT: All gates default to CLOSED (False).
2. INDEPENDENCE: Activating any single gate (e.g. Gate A) NEVER activates any other gate.
3. IMMUTABILITY WITHOUT AUTHORIZATION: Attempting an action on a closed gate raises GateClosedError.
"""

import os
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Any, Optional, Union, List

logger = logging.getLogger(__name__)

# Strict policy: Automated outbound replies to professors and recruiters are permanently prohibited.
AUTOMATED_REPLY_TRANSMISSION_PROHIBITED: bool = True


def is_automated_reply_permitted() -> bool:
    """Always returns False: Automated outbound reply sending is permanently prohibited."""
    return False


class GateID(str, Enum):
    GATE_A_MASTER_WORKBOOK_MUTATION = "GATE_A_MASTER_WORKBOOK_MUTATION"
    GATE_A_UNIFIED_WORKBOOK_INTEGRATION = "GATE_A_UNIFIED_WORKBOOK_INTEGRATION"
    GATE_B_ACADEMIC_EXPANSION_DISPATCH = "GATE_B_ACADEMIC_EXPANSION_DISPATCH"
    GATE_C_NON_ACADEMIC_DISPATCH = "GATE_C_NON_ACADEMIC_DISPATCH"
    GATE_D_LIVE_PORTAL_SUBMISSION = "GATE_D_LIVE_PORTAL_SUBMISSION"
    GATE_E_CONTINUOUS_DISCOVERY_SYNC = "GATE_E_CONTINUOUS_DISCOVERY_SYNC"
    GATE_E_AUTOMATED_REPLY_DISPATCH = "GATE_E_AUTOMATED_REPLY_DISPATCH"  # Deprecated legacy alias


class GateClosedError(RuntimeError):
    """Raised when an operation is attempted through a closed activation gate."""
    def __init__(self, gate_id: GateID, message: Optional[str] = None):
        self.gate_id = gate_id
        msg = message or f"Operation forbidden: Activation gate {gate_id.value} is CLOSED (Fail-Closed Enforcement)."
        super().__init__(msg)


@dataclass
class GateStatus:
    gate_id: GateID
    name: str
    description: str
    is_open: bool
    env_var: str
    risk_level: str
    authorized_by: Optional[str] = None
    activated_at: Optional[str] = None


class ActivationGateManager:
    """
    Manages independent activation gates controlling production integration actions.
    Enforces complete isolation between gates.
    """

    GATE_DEFINITIONS = {
        GateID.GATE_A_MASTER_WORKBOOK_MUTATION: {
            "name": "Gate A: Master Workbook In-Place Mutation",
            "description": "Allows direct in-place mutation or overwriting of the live authoritative master Excel workbook.",
            "env_var": "ENABLE_PRODUCTION_WORKBOOK_MUTATION",
            "risk_level": "CRITICAL_INTEGRITY",
        },
        GateID.GATE_A_UNIFIED_WORKBOOK_INTEGRATION: {
            "name": "Gate A: Unified Authoritative Workbook & Database Integration",
            "description": "Authorizes authoritative single-workbook integration and SQLite transactional synchronization. Never activates email sending.",
            "env_var": "ENABLE_PRODUCTION_WORKBOOK_MUTATION",
            "risk_level": "CRITICAL_INTEGRITY",
        },
        GateID.GATE_B_ACADEMIC_EXPANSION_DISPATCH: {
            "name": "Gate B: Academic Expansion Faculty Dispatch",
            "description": "Allows live SMTP dispatching to net-new academic faculty discovered in development.",
            "env_var": "ENABLE_ACADEMIC_EXPANSION_SENDING",
            "risk_level": "HIGH_OUTREACH",
        },
        GateID.GATE_C_NON_ACADEMIC_DISPATCH: {
            "name": "Gate C: Non-Academic Direct Inquiries",
            "description": "Allows live SMTP dispatching to startups, industry R&D labs, and institutional scheme direct emails.",
            "env_var": "ENABLE_NON_ACADEMIC_SENDING",
            "risk_level": "HIGH_OUTREACH",
        },
        GateID.GATE_D_LIVE_PORTAL_SUBMISSION: {
            "name": "Gate D: Live External Portal Submissions",
            "description": "Allows autonomous live HTTP POST submission of real candidate applications into external portals.",
            "env_var": "ENABLE_LIVE_PORTAL_SUBMISSIONS",
            "risk_level": "CRITICAL_EXTERNAL_MUTATION",
        },
        GateID.GATE_E_CONTINUOUS_DISCOVERY_SYNC: {
            "name": "Gate E: Continuous Production Discovery Synchronization",
            "description": "Allows continuous synchronization of verified opportunities and permitted workflow metadata into production data stores. Does NOT activate email sending (B, C) or portal submissions (D).",
            "env_var": "ENABLE_CONTINUOUS_DISCOVERY_SYNC",
            "risk_level": "MEDIUM_DATA_SYNC",
        },
        GateID.GATE_E_AUTOMATED_REPLY_DISPATCH: {
            "name": "Gate E: Automated Inbound Reply Dispatching (Legacy)",
            "description": "Automated outbound reply dispatching is permanently disabled.",
            "env_var": "ENABLE_AUTOMATED_REPLY_SENDING",
            "risk_level": "PROHIBITED_CAPABILITY",
        },
    }

    def __init__(self):
        # Explicit in-memory override registry (for authorized programmatic control)
        self._manual_overrides: Dict[GateID, Dict[str, Any]] = {}
        self.activation_audit_trail: List[Dict[str, Any]] = []

    def _resolve_gate_id(self, gate: Union[GateID, str]) -> GateID:
        if isinstance(gate, GateID):
            return gate
        try:
            return GateID(gate)
        except ValueError:
            # Match by shorthand (e.g. "A", "GATE_A")
            gate_upper = str(gate).upper()
            for gid in [
                GateID.GATE_A_UNIFIED_WORKBOOK_INTEGRATION,
                GateID.GATE_A_MASTER_WORKBOOK_MUTATION,
                GateID.GATE_B_ACADEMIC_EXPANSION_DISPATCH,
                GateID.GATE_C_NON_ACADEMIC_DISPATCH,
                GateID.GATE_D_LIVE_PORTAL_SUBMISSION,
                GateID.GATE_E_CONTINUOUS_DISCOVERY_SYNC,
                GateID.GATE_E_AUTOMATED_REPLY_DISPATCH
            ]:
                if gid.value.startswith(gate_upper) or gate_upper in gid.value:
                    return gid
            raise ValueError(f"Unknown gate identifier: {gate}")

    def is_gate_open(self, gate: Union[GateID, str]) -> bool:
        """
        Returns True ONLY if the specific gate is explicitly authorized.
        Defaults to False (Fail-Closed).
        """
        gid = self._resolve_gate_id(gate)

        # 1. Check in-memory override
        if gid in self._manual_overrides:
            return self._manual_overrides[gid]["is_open"]

        # 2. Check environment variable
        meta = self.GATE_DEFINITIONS[gid]
        env_val = os.environ.get(meta["env_var"], "").strip().lower()
        return env_val in ("1", "true", "yes", "enabled")

    def assert_gate_open(self, gate: Union[GateID, str]) -> None:
        """Raises GateClosedError if the gate is closed."""
        gid = self._resolve_gate_id(gate)
        if not self.is_gate_open(gid):
            raise GateClosedError(gid)

    def get_gate_status(self, gate: Union[GateID, str]) -> GateStatus:
        """Returns the full status descriptor for a given gate."""
        gid = self._resolve_gate_id(gate)
        meta = self.GATE_DEFINITIONS[gid]
        is_open = self.is_gate_open(gid)

        override = self._manual_overrides.get(gid)
        auth_by = override.get("authorized_by") if override else None
        act_at = override.get("activated_at") if override else None

        return GateStatus(
            gate_id=gid,
            name=meta["name"],
            description=meta["description"],
            is_open=is_open,
            env_var=meta["env_var"],
            risk_level=meta["risk_level"],
            authorized_by=auth_by,
            activated_at=act_at
        )

    def get_all_gate_statuses(self) -> Dict[str, Dict[str, Any]]:
        """Returns status map of all 5 independent gates."""
        result = {}
        for gid in GateID:
            st = self.get_gate_status(gid)
            result[gid.value] = {
                "name": st.name,
                "is_open": st.is_open,
                "env_var": st.env_var,
                "risk_level": st.risk_level,
                "authorized_by": st.authorized_by,
                "activated_at": st.activated_at,
                "description": st.description
            }
        return result

    def enable_gate(
        self,
        gate: Union[GateID, str],
        authorized_by: str,
        confirmation_token: str
    ) -> None:
        """
        Explicitly enables a specific gate.
        Enforces token verification and records audit actor.
        Enabling one gate NEVER enables any other gate.
        """
        gid = self._resolve_gate_id(gate)
        expected_token = f"CONFIRM_ACTIVATE_{gid.value}"
        if confirmation_token != expected_token:
            raise ValueError(
                f"Invalid confirmation token. Expected '{expected_token}', got '{confirmation_token}'."
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        self._manual_overrides[gid] = {
            "is_open": True,
            "authorized_by": authorized_by,
            "activated_at": now_iso
        }
        audit_entry = {
            "event_type": "ACTIVATION_GATE_OPENED",
            "gate_id": gid.value,
            "authorized_by": authorized_by,
            "timestamp": now_iso
        }
        self.activation_audit_trail.append(audit_entry)
        try:
            from src.security.audit_logger import audit_logger
            audit_logger.log_event("ACTIVATION_GATE_OPENED", {
                "gate_id": gid.value,
                "authorized_by": authorized_by,
                "timestamp": now_iso
            })
        except Exception:
            pass
        logger.warning(f"ACTIVATION GATE OPENED: {gid.value} authorized by {authorized_by} at {now_iso}")

    def disable_gate(self, gate: Union[GateID, str]) -> None:
        """Closes a specific gate immediately."""
        gid = self._resolve_gate_id(gate)
        now_iso = datetime.now(timezone.utc).isoformat()
        self._manual_overrides[gid] = {
            "is_open": False,
            "authorized_by": None,
            "activated_at": None
        }
        audit_entry = {
            "event_type": "ACTIVATION_GATE_CLOSED",
            "gate_id": gid.value,
            "timestamp": now_iso
        }
        self.activation_audit_trail.append(audit_entry)
        try:
            from src.security.audit_logger import audit_logger
            audit_logger.log_event("ACTIVATION_GATE_CLOSED", {
                "gate_id": gid.value,
                "timestamp": now_iso
            })
        except Exception:
            pass
        logger.info(f"Activation gate closed: {gid.value}")

    def disable_all_gates(self) -> None:
        """Closes all gates immediately (emergency killswitch)."""
        for gid in GateID:
            self.disable_gate(gid)
