"""Shared permission-scoped recruiting reads; never create Tasks or change stages."""
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, CharField, Value, Prefetch
from django.db.models.functions import Cast, Replace
from django.utils import timezone
from apps.participants.identity import identity_members
from apps.operations.task_access import tasks_for
from .access import fresh_staff
from .selection import opportunities_for
from .models import CandidateOpportunity, CandidateInterest, EmployerCandidateFeedback, RecruitingTaskLink


def staff_queues(actor, *, include_followup_tasks=True):
    actor = fresh_staff(actor)
    items = opportunities_for(actor).filter(archived_at__isnull=True)
    from .employer import feedback_reviews
    from apps.operations.models import Task
    candidates = CandidateOpportunity.objects.filter(opportunity__in=items)
    responses = EmployerCandidateFeedback.objects.filter(disclosure__candidate__opportunity__in=items).select_related(
        'disclosure__candidate__participant', 'disclosure__need__effective_revision')
    reviews = feedback_reviews().annotate(review_id=Replace('object_id', Value('-'), Value(''))).filter(
        review_id=Replace(Cast(OuterRef('pk'), output_field=CharField()), Value('-'), Value('')))
    pending = responses.annotate(staff_reviewed=Exists(reviews)).filter(staff_reviewed=False)
    overdue = (tasks_for(actor).filter(pk__in=RecruitingTaskLink.objects.filter(opportunity__in=items).values('task_id'),
        archived_at__isnull=True, due_date__lt=timezone.localdate(), status__in=[Task.Status.TODO, Task.Status.PROGRESS])
        if include_followup_tasks else None)
    return {'items': items, 'current': items.filter(need__status='active', need__effective_revision__state='approved'),
        'candidates': candidates, 'responses': responses, 'pending_responses': pending, 'overdue': overdue,
        'incoming': CandidateInterest.objects.filter(opportunity__in=items, active=True, status='received'),
        'outreach': candidates.filter(active=True, stage_id='contacting', outreach__isnull=True).select_related(
            'participant', 'opportunity__need__effective_revision')}


def participant_context(actor, participant, *, page=None):
    items = opportunities_for(actor).filter(archived_at__isnull=True)
    members = identity_members(participant.pk)
    records = CandidateOpportunity.objects.filter(participant_id__in=members, opportunity__in=items).select_related(
        'stage', 'opportunity__need__organization', 'opportunity__need__effective_revision', 'opportunity__need__campaign').prefetch_related(
        Prefetch('originating_interests', queryset=CandidateInterest.objects.filter(active=True, participant_id__in=members, opportunity__in=items).select_related('publication').order_by('-created_at','-pk')[:1], to_attr='display_interests')).order_by('-active', '-created_at', '-pk')
    interests = CandidateInterest.objects.filter(participant_id__in=members, opportunity__in=items, active=True).select_related(
        'publication').order_by('-created_at', '-pk')[:3]
    from apps.customers.access import is_manager
    return {'profile_recruiting_customer_links': is_manager(actor), 'profile_recruiting_records': Paginator(records, 6).get_page(page),
        'profile_recruiting_interests': list(interests)}
