"""Future disclosure contract. Build 14 can never return participant information."""
from dataclasses import dataclass
from django.core.exceptions import PermissionDenied

FUTURE_SEARCH_DIMENSIONS = ('trade_occupation', 'skills', 'certifications', 'experience', 'general_location', 'willingness_to_travel')


@dataclass(frozen=True)
class DirectoryDecision:
    allowed: bool = False
    reason: str = 'Customer participant disclosure is not enabled in Build 14.'


def visibility_decision(*, actor, organization_id):
    # Entitlements, participant self-consent and staff portal previews cannot bypass this gate.
    return DirectoryDecision()


def safe_query(*, actor, organization_id, dimensions=None):
    raise PermissionDenied(DirectoryDecision().reason)


def safe_projection(*, actor, organization_id, participant=None):
    raise PermissionDenied(DirectoryDecision().reason)


def audit_contract():
    # A later release must record authorized purpose, actor, organization, policy
    # version and disclosure categories in central AuditLog before any disclosure.
    # Search input, contact details and returned participant identifiers are never
    # public audit payloads. No query or access log is exposed to customers now.
    return ('actor', 'organization', 'authorized_purpose', 'policy_version', 'disclosure_categories')
