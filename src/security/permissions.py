"""
Security and Application-Level Permissions Boundary Module.

Enforces strict separation between Autonomous AI capabilities and Human Operator actions.
The AI is structurally barred at the application code level from transmitting replies
or follow-up emails.
"""

from enum import Enum
from typing import Optional


class SecurityRole(str, Enum):
    """Application-level security roles."""
    AUTONOMOUS_AI_ENGINE = "AUTONOMOUS_AI_ENGINE"
    HUMAN_OPERATOR = "HUMAN_OPERATOR"
    SYSTEM_CRON = "SYSTEM_CRON"


class SecurityAction(str, Enum):
    """Protected operations within the Outreach Engine."""
    VIEW_DATA = "VIEW_DATA"
    IMPORT_WORKBOOK = "IMPORT_WORKBOOK"
    GENERATE_INITIAL_DRAFT = "GENERATE_INITIAL_DRAFT"
    SCHEDULE_OUTREACH = "SCHEDULE_OUTREACH"
    CLASSIFY_REPLY = "CLASSIFY_REPLY"
    GENERATE_REPLY_DRAFT = "GENERATE_REPLY_DRAFT"
    GENERATE_FOLLOWUP_DRAFT = "GENERATE_FOLLOWUP_DRAFT"
    DISPATCH_INITIAL_BATCH = "DISPATCH_INITIAL_BATCH"
    SEND_REPLY_TRANSMISSION = "SEND_REPLY_TRANSMISSION"
    SEND_FOLLOWUP_TRANSMISSION = "SEND_FOLLOWUP_TRANSMISSION"
    MANUAL_SUPPRESSION_UPDATE = "MANUAL_SUPPRESSION_UPDATE"
    EMERGENCY_STOP_TOGGLE = "EMERGENCY_STOP_TOGGLE"
    AUTHORIZE_CAMPAIGN = "AUTHORIZE_CAMPAIGN"
    WORKBOOK_SYNC_WRITE = "WORKBOOK_SYNC_WRITE"


class PermissionDeniedError(Exception):
    """Raised when an actor attempts an unauthorized action."""
    pass


class CampaignAuthorizationError(Exception):
    """Raised when dispatch is attempted without full institutional and user approvals."""
    pass


# Strict permission matrix
# Note that SEND_REPLY_TRANSMISSION and SEND_FOLLOWUP_TRANSMISSION are ONLY permitted for HUMAN_OPERATOR.
PERMISSIONS = {
    SecurityRole.AUTONOMOUS_AI_ENGINE: {
        SecurityAction.VIEW_DATA,
        SecurityAction.IMPORT_WORKBOOK,
        SecurityAction.GENERATE_INITIAL_DRAFT,
        SecurityAction.SCHEDULE_OUTREACH,
        SecurityAction.CLASSIFY_REPLY,
        SecurityAction.GENERATE_REPLY_DRAFT,
        SecurityAction.GENERATE_FOLLOWUP_DRAFT,
        # Notice: SEND_REPLY_TRANSMISSION and SEND_FOLLOWUP_TRANSMISSION ARE ABSOLUTELY OMITTED.
    },
    SecurityRole.HUMAN_OPERATOR: {
        SecurityAction.VIEW_DATA,
        SecurityAction.IMPORT_WORKBOOK,
        SecurityAction.GENERATE_INITIAL_DRAFT,
        SecurityAction.SCHEDULE_OUTREACH,
        SecurityAction.CLASSIFY_REPLY,
        SecurityAction.GENERATE_REPLY_DRAFT,
        SecurityAction.GENERATE_FOLLOWUP_DRAFT,
        SecurityAction.DISPATCH_INITIAL_BATCH,
        SecurityAction.SEND_REPLY_TRANSMISSION,
        SecurityAction.SEND_FOLLOWUP_TRANSMISSION,
        SecurityAction.MANUAL_SUPPRESSION_UPDATE,
        SecurityAction.EMERGENCY_STOP_TOGGLE,
        SecurityAction.AUTHORIZE_CAMPAIGN,
        SecurityAction.WORKBOOK_SYNC_WRITE,
    },
    SecurityRole.SYSTEM_CRON: {
        SecurityAction.VIEW_DATA,
        SecurityAction.CLASSIFY_REPLY,
        SecurityAction.SCHEDULE_OUTREACH,
    }
}


def require_permission(action: SecurityAction, actor: SecurityRole) -> None:
    """
    Enforce that the specified actor possesses explicit permission for the action.
    Raises PermissionDeniedError immediately if unauthorized.
    """
    allowed_actions = PERMISSIONS.get(actor, set())
    if action not in allowed_actions:
        raise PermissionDeniedError(
            f"PERMISSION DENIED: Role '{actor.value}' is strictly forbidden from executing '{action.value}'."
        )


def assert_human_only_transmission(action: SecurityAction, actor: SecurityRole, operator_id: Optional[str]) -> None:
    """
    Guarantees that reply or follow-up transmission is executed by a verified human operator.
    """
    if actor != SecurityRole.HUMAN_OPERATOR:
        raise PermissionDeniedError(
            f"CRITICAL SAFETY VIOLATION: '{action.value}' requires explicit HUMAN_OPERATOR execution. "
            f"Autonomous AI agents are strictly forbidden from autonomous transmissions."
        )
    if not operator_id or not operator_id.strip():
        raise PermissionDeniedError(
            f"CRITICAL SAFETY VIOLATION: '{action.value}' requires a non-empty human operator identifier."
        )


def validate_campaign_preflight(
    gateway_enabled: bool,
    campaign_authorized: bool,
    csc_approved: bool,
    emergency_stop: bool,
    dry_run: bool = True
) -> None:
    """
    Validates all mandatory gates before any initial outreach dispatch is allowed.
    Even in dry-run mode, emergency stop and basic integrity are verified.
    """
    if emergency_stop:
        raise CampaignAuthorizationError("DISPATCH BLOCKED: Emergency Stop is currently ACTIVE.")
    
    # If not a dry-run, strictly verify operational permissions
    if not dry_run:
        if not gateway_enabled:
            raise CampaignAuthorizationError("DISPATCH BLOCKED: Sending Gateway is DISABLED.")
        if not campaign_authorized:
            raise CampaignAuthorizationError("DISPATCH BLOCKED: User has not explicitly authorized this campaign.")
        if not csc_approved:
            raise CampaignAuthorizationError(
                "DISPATCH BLOCKED: IIT Delhi CSC automation approval has not been confirmed."
            )
