"""18B settings extend the existing policy; recipients receive no new authority."""
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError

from .models import TaskReminderPolicy

EVENT_BOOLEANS = ('staffing_enabled', 'preparation_enabled', 'closeout_enabled',
                  'staffing_reviews_enabled')
COMPANY_BOOLEANS = ('verification_reviews_enabled', 'job_reviews_enabled', 'customer_reviews_enabled')
TIMINGS = {'staffing_days': ((7, 2), (10, 3), (14, 7)),
           'preparation_days': ((3, 1), (5, 2), (7, 3)),
           'closeout_days': (3, 5, 7)}
RECIPIENT_FIELDS = {'staffing': 'staffing_review_recipient',
                    'verification': 'verification_review_recipient',
                    'jobs': 'job_review_recipient', 'customers': 'customer_review_recipient'}
FIELDS = (*EVENT_BOOLEANS, *COMPANY_BOOLEANS, *TIMINGS, *RECIPIENT_FIELDS.values())
DEFAULTS = {**dict.fromkeys((*EVENT_BOOLEANS, *COMPANY_BOOLEANS), True),
            **{name: choices[0] for name, choices in TIMINGS.items()}}


def eligible_reviewer(user, category):
    if not user or not user.is_active or not user.is_staff:
        return False
    if category == 'staffing':
        from apps.staffing.services import require_manager
        try:
            require_manager(user)
        except PermissionDenied:
            return False
        return True
    if category == 'verification':
        from apps.participants.verification_services import can_review_qualification_approvals
        return can_review_qualification_approvals(user)
    if category in ('jobs', 'customers'):
        from apps.customers.access import is_manager
        return is_manager(user)
    return False


def eligible_recipients(category):
    candidates = get_user_model().objects.filter(is_active=True, is_staff=True)
    ids = [user.pk for user in candidates if eligible_reviewer(user, category)]
    return candidates.filter(pk__in=ids).order_by('first_name', 'last_name', 'username')


def reviewer(category):
    field = RECIPIENT_FIELDS[category]
    policy = TaskReminderPolicy.objects.select_related(field).filter(pk='company').first()
    configured = getattr(policy, field) if policy else None
    if configured is None:
        return None
    # No cached account object may survive a permissions or account change.
    current = get_user_model().objects.filter(pk=configured.pk).first()
    return current if eligible_reviewer(current, category) else None


def values(event=None):
    keys = ['company'] + (['event:' + str(event.pk)] if event else [])
    policies = {policy.key: policy for policy in TaskReminderPolicy.objects.filter(pk__in=keys)}
    result = dict(DEFAULTS)
    for key in keys:
        policy = policies.get(key)
        if policy:
            for field in DEFAULTS:
                value = getattr(policy, field)
                if value is not None:
                    result[field] = value
    return result


def snapshot(policy, names):
    result = {}
    for name in names:
        value = getattr(policy, name + '_id' if name in RECIPIENT_FIELDS.values() else name)
        result[name] = str(value) if name in RECIPIENT_FIELDS.values() and value else value
    return result


def apply(policy, submitted, *, event=None):
    supplied = [field for field in FIELDS if field in submitted]
    if event and any(field in supplied for field in (*COMPANY_BOOLEANS, *RECIPIENT_FIELDS.values())):
        raise PermissionDenied
    for field in supplied:
        value = submitted[field]
        if field in (*EVENT_BOOLEANS, *COMPANY_BOOLEANS):
            if (value is None and not event) or (value is not None and type(value) is not bool):
                raise ValidationError({field: 'Choose a reminder setting.'})
        elif field in TIMINGS:
            if value is None:
                if not event:
                    raise ValidationError({field: 'Choose a reminder time.'})
            else:
                normalized = tuple(value) if isinstance(value, (list, tuple)) else value
                if normalized not in TIMINGS[field]:
                    raise ValidationError({field: 'Choose a reminder time.'})
                value = list(normalized) if isinstance(normalized, tuple) else normalized
        else:
            category = next(key for key, name in RECIPIENT_FIELDS.items() if name == field)
            if value is not None:
                value = get_user_model().objects.filter(pk=getattr(value, 'pk', value)).first()
                if not eligible_reviewer(value, category):
                    raise ValidationError({field: 'Choose an eligible account.'})
        setattr(policy, field, value)
    return supplied
