"""Customer submissions extend the existing domain; BTW alone decides reviews."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from . import services as s
from .models import Campaign, WorkforceNeed, CustomerFeedback
from .portal_policy import is_customer


@transaction.atomic
def campaign_submission(*, actor, org_id, expected_org_revision, campaign_id, expected_revision, action):
    customer = is_customer(actor)
    if action not in ({'submit', 'withdraw'} if customer else {'submit', 'withdraw', 'review', 'return'}):
        raise PermissionDenied('This Campaign review action requires BTW authority.')
    actor, org = s.locked_org(actor, org_id, expected_org_revision, customer_action='campaigns')
    obj = Campaign.objects.select_for_update().get(pk=campaign_id, organization=org)
    s.expected(obj, expected_revision, 'Campaign')
    if obj.status in {'completed', 'cancelled'}:
        raise ValidationError('This Campaign is complete or cancelled.')
    if action == 'submit' and obj.review_state in {'draft', 'returned'}:
        if not obj.name.strip() or not obj.description.strip() or not obj.objectives.strip():
            raise ValidationError('Provide a name, description and objectives before submission.')
        obj.review_state = 'submitted'
        obj.submitted_planning_version = obj.planning_version
        obj.submitted_at, obj.submitted_by = timezone.now(), actor
    elif action in {'withdraw', 'return'} and obj.review_state in {'submitted', 'review'}:
        obj.review_state = 'draft' if action == 'withdraw' else 'returned'
    elif action == 'review' and obj.review_state == 'submitted':
        obj.review_state = 'review'
    else:
        raise ValidationError('This review action is not available for the current planning version.')
    s.touch(obj, 'review_state', 'submitted_planning_version', 'submitted_at', 'submitted_by')
    s.touch(org)
    s.audit(actor, org, 'campaign.' + action, obj, after={'planning_version': obj.planning_version, 'review_state': obj.review_state}, why='Explicit Campaign submission/review action.', campaign=obj)
    return obj


@transaction.atomic
def publish_feedback(*, actor, org_id, expected_org_revision, kind, record_id, expected_revision, body, action='feedback'):
    actor, org = s.locked_org(actor, org_id, expected_org_revision)
    if kind not in {'need', 'campaign'} or action not in {'feedback', 'review', 'return'}:
        raise ValidationError('Choose a supported review action.')
    model = WorkforceNeed if kind == 'need' else Campaign
    obj = model.objects.select_for_update().get(pk=record_id, organization=org)
    s.expected(obj, expected_revision, kind)
    subject_version = obj.planning_version if kind == 'campaign' else (obj.working_revision or obj.effective_revision).number if (obj.working_revision_id or obj.effective_revision_id) else None
    body = str(body or '').strip()
    if not body or len(body) > 4000:
        raise ValidationError('Write customer-visible feedback of up to 4,000 characters.')
    if action != 'feedback':
        if kind == 'need':
            s.revision_action(actor=actor, org_id=org.pk, need_id=obj.pk, expected_org_revision=org.revision,
                expected_need_revision=obj.revision, revision_id=obj.working_revision_id, action=action,
                why='BTW review action. Deliberately published feedback is stored separately.')
        else:
            campaign_submission(actor=actor, org_id=org.pk, expected_org_revision=org.revision,
                campaign_id=obj.pk, expected_revision=obj.revision, action=action)
    entry = s.audit(actor, org, 'feedback.published', obj, after={'subject_version': subject_version}, why='BTW deliberately published customer-visible feedback.',
                    need=obj if kind == 'need' else None, campaign=obj if kind == 'campaign' else None)
    return CustomerFeedback.objects.create(organization=org, workforce_need=obj if kind == 'need' else None,
        campaign=obj if kind == 'campaign' else None, body=body, author=actor, audit=entry)
