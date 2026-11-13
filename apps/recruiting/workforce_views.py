"""A scoped starting point over existing Needs, Requests and recruiting records."""
from urllib.parse import urlencode
from django import forms
from django.core.paginator import Paginator
from django.db.models import Q, Count, Exists, OuterRef
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from apps.accounts.roles import manager
from apps.customers.access import is_manager as customer_manager
from apps.customers.models import WorkforceNeed, CustomerOrganization
from .access import fresh_staff
from .models import WorkforceRequest, RecruitingOpportunity
from .selection import opportunities_for
from .views import staff, page


def scoped_needs(actor):
    actor = fresh_staff(actor)
    needs = WorkforceNeed.objects.select_related('organization', 'effective_revision__location', 'working_revision__location', 'campaign')
    if not customer_manager(actor):
        needs = needs.filter(pk__in=opportunities_for(actor).filter(archived_at__isnull=True).values('need_id'))
    return needs


class NamedRecordChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.name


class WorkforceFilters(forms.Form):
    q = forms.CharField(required=False, max_length=200, label='Position or customer')
    status = forms.ChoiceField(required=False, choices=[('open', 'Open positions'), ('all', 'All statuses')] + list(WorkforceNeed.Status.choices))
    customer = NamedRecordChoice(required=False, queryset=CustomerOrganization.objects.none(), empty_label='Any customer')
    request = NamedRecordChoice(required=False, queryset=WorkforceRequest.objects.none(), label='Position group', empty_label='Any group')

    def __init__(self, *args, needs, can_manage_requests, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['customer'].queryset = CustomerOrganization.objects.filter(workforce_needs__in=needs).distinct().order_by('name')
        if can_manage_requests:
            self.fields['request'].queryset = WorkforceRequest.objects.filter(archived_at__isnull=True, positions__need__in=needs).distinct().order_by('name')
        else:
            self.fields.pop('request')

    def clean(self):
        data = super().clean()
        if self.data.get('request') and 'request' not in self.fields:
            self.add_error(None, 'Workforce Requests require customer-management access.')
        return data


@staff
@require_http_methods(['GET'])
def directory(request):
    actor = request.recruiting_actor
    can_customers = customer_manager(actor)
    base = scoped_needs(actor)
    form = WorkforceFilters(request.GET, needs=base, can_manage_requests=can_customers)
    needs = base
    if form.is_valid():
        d = form.cleaned_data
        state = d.get('status') or 'open'
        if state == 'open':
            needs = needs.exclude(status__in=['closed', 'cancelled'])
        elif state != 'all':
            needs = needs.filter(status=state)
        if d.get('q'):
            search = Q(effective_revision__title__icontains=d['q']) | Q(organization__name__icontains=d['q'])
            if can_customers:
                search |= Q(working_revision__title__icontains=d['q'])
            needs = needs.filter(search)
        if d.get('customer'):
            needs = needs.filter(organization=d['customer'])
        if d.get('request'):
            needs = needs.filter(request_position__request=d['request'])
    else:
        needs = needs.none()
    needs = needs.annotate(has_recruiting_setup=Exists(RecruitingOpportunity.objects.filter(need_id=OuterRef('pk'))))
    records = Paginator(needs.order_by('-updated_at', 'pk'), 20).get_page(request.GET.get('page'))
    opportunities = {item.need_id: item for item in opportunities_for(actor).filter(need_id__in=[n.pk for n in records], archived_at__isnull=True)}
    from apps.customers.position_view import pay_text
    rows = []
    for need in records:
        approved = bool(need.effective_revision and need.effective_revision.state == 'approved')
        terms = need.effective_revision if approved else need.working_revision if can_customers else None
        site = terms.location if terms and terms.location_mode == 'location' else None
        location = ', '.join(v for v in ([site.city, site.state] if site else [terms.city, terms.location_state] if terms else []) if v)
        item = opportunities.get(need.pk)
        ready = approved and need.status == 'active'
        rows.append({
            'title': terms.title if terms else 'Requirements unavailable', 'customer': need.organization.name, 'status': need.get_status_display(),
            'approved': approved, 'quantity': terms.worker_quantity if terms else None,
            'location': location or (site.name if site else ''),
            'trade': terms.trade if terms else '', 'description': terms.description if terms else '',
            'employment': terms.get_employment_type_display() if terms else '',
            'pay': pay_text(terms) if terms else '',
            'campaign': need.campaign.name if need.campaign else '',
            'campaign_url': reverse('recruiting:campaign-detail', args=[need.campaign_id]) if need.campaign_id else '',
            'need_url': reverse('customers:need', args=[need.organization_id, need.pk]) if can_customers else '',
            'match_url': reverse('recruiting:matching', args=[item.pk]) if item and ready else '',
            'setup_url': reverse('recruiting:matching-directory') + '?' + urlencode({'need': need.pk}) if ready and not need.has_recruiting_setup and manager(actor) else '',
            'pipeline_url': reverse('recruiting:pipeline') + '?' + urlencode({'opportunity': item.pk}) if item else '',
        })
    # Request names remain within the existing customer-manager boundary. Count
    # only visible positions; a Request is not a position or worker quantity.
    requests = (WorkforceRequest.objects.filter(archived_at__isnull=True, positions__need__in=needs)
        .select_related('organization').annotate(visible_positions=Count('positions', filter=Q(positions__need__in=needs), distinct=True)).distinct().order_by('name') if can_customers else WorkforceRequest.objects.none())
    query = request.GET.copy()
    query.pop('page', None)
    request_rows = list(requests[:20])
    for item in request_rows:
        request_query = query.copy()
        request_query['request'] = str(item.pk)
        item.filter_url = '?' + request_query.urlencode()
    return page(request, 'recruiting/workforce_needs.html', page_title='Positions',
        filters=form, needs_page=records, need_rows=rows, requests=request_rows, request_count=requests.count(),
        can_manage_requests=can_customers, query_without_page=query.urlencode(),
        active_filters=(sum(bool(form.cleaned_data.get(k)) for k in ('q', 'customer', 'request'))
            + int(form.cleaned_data.get('status') not in ('', None, 'open'))) if not form.errors else 0)
