from django import template
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from apps.operations.task_access import require_task_access
from apps.recruiting.selection import opportunity_for
from apps.recruiting.models import RecruitingTaskLink
register=template.Library()


@register.simple_tag
def recruiting_task_contexts(actor,task):
    require_task_access(actor,task)
    result=[]
    for link in RecruitingTaskLink.objects.filter(task=task).select_related('opportunity__need__effective_revision','candidate','interest'):
        try:item=opportunity_for(actor,link.opportunity_id)
        except PermissionDenied:
            result.append({'label':'Restricted recruiting context','url':None});continue
        url=reverse('recruiting:candidate-detail',args=[link.candidate_id]) if link.candidate_id else reverse('recruiting:interest-review',args=[link.interest_id]) if link.interest_id else reverse('recruiting:matching',args=[item.pk])
        result.append({'label':item.need.title,'url':url})
    return result
