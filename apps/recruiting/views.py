from functools import wraps
from django.forms import BaseForm
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from apps.participants.models import Participant
from apps.participants.identity import participant_candidates
from .access import fresh_staff, person, policy_transaction
from .preferences import initial_preferences
from .models import CandidateList, RecruitingProfile, Classification, CampaignRecruitingPolicy
from .forms import PreferencesForm, ListForm, ConsolidationForm, ClassificationForm, CampaignPolicyForm,CampaignSourceForm
from . import foundation as service


def staff(view):
    @login_required(login_url='accounts:login')
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        request.recruiting_actor = fresh_staff(request.user)
        response = view(request, *args, **kwargs)
        response['Cache-Control'] = 'private, no-store'
        return response
    return wrapped


def page(request, template, **context):
    context.setdefault('page_title', 'Recruiting')
    context.setdefault('active_workspace', 'participants')
    # Independent forms keep their POST contracts but must not share HTML IDs.
    def label_forms(value, key):
        if isinstance(value, BaseForm):
            value.auto_id = 'recruiting_' + key + '_%s'
        elif isinstance(value, dict):
            for name, child in value.items():
                label_forms(child, key + '_' + str(name))
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                label_forms(child, key + '_' + str(index))
    for key, value in context.items():
        label_forms(value, key)
    return render(request, template, context)


@staff
@require_http_methods(['GET', 'POST'])
def lists(request):
    form = ListForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            item = service.save_list(actor=request.user, name=form.cleaned_data['name'], shared=form.cleaned_data['shared'])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return redirect('recruiting:list', list_id=item.pk)
    return page(request, 'recruiting/lists.html', form=form,
        lists=service.visible_lists(request.user).filter(archived_at__isnull=True), page_title='Candidate lists')


@staff
@require_http_methods(['GET', 'POST'])
def candidate_list(request, list_id):
    item = get_object_or_404(service.visible_lists(request.user), pk=list_id)
    if request.method == 'POST':
        try:
            if request.POST.get('operation') in ['archive', 'restore']:
                service.save_list(actor=request.user, name=item.name, shared=item.shared, object_id=item.pk,
                    expected_revision=int(request.POST.get('revision', -1)), archive=request.POST['operation'] == 'archive')
                return redirect('recruiting:lists')
            service.change_list_member(actor=request.user, list_id=item.pk,
                participant_id=int(request.POST.get('participant_id', 0)), remove=request.POST.get('operation') == 'remove', why=request.POST.get('reason'))
        except (ValueError, Participant.DoesNotExist):
            messages.error(request, 'Choose an available person from the existing Participants directory.')
        except ValidationError as exc:
            messages.error(request, '; '.join(exc.messages))
        else:
            return redirect('recruiting:list', list_id=item.pk)
    return page(request, 'recruiting/list.html', candidate_list=item,
        entries=item.entries.filter(removed_at__isnull=True).select_related('participant', 'added_by'),
        editable=item.owner_id == request.user.pk or request.recruiting_actor.is_superuser or service.manager(request.recruiting_actor),
        page_title=item.name)


@staff
@require_http_methods(['GET', 'POST'])
def candidate_foundation(request, participant_id):
    try:
        item = person(participant_id)
    except Participant.DoesNotExist:
        raise Http404
    row = RecruitingProfile.objects.filter(participant=item).first()
    from .preferences import initial_preferences
    initial = initial_preferences(item)
    form = PreferencesForm(request.POST if request.method == 'POST' and request.POST.get('operation') == 'preferences' else None, initial=initial)
    source_form=CampaignSourceForm(request.POST if request.method=='POST' and request.POST.get('operation')=='source_campaign' else None,actor=request.recruiting_actor)
    if request.method == 'POST':
        try:
            action = request.POST.get('operation')
            if action == 'preferences' and form.is_valid():
                service.save_preferences(actor=request.user, participant_id=item.pk, data=form.cleaned_data,
                    expected_revision=form.cleaned_data['revision'], why=form.cleaned_data['reason'])
            elif action == 'confirm':
                service.confirm_profile(actor=request.user, participant_id=item.pk,
                    expected_fingerprint=request.POST.get('fingerprint'), why=request.POST.get('reason'))
            elif action == 'request_update':
                service.request_update(actor=request.user, participant_id=item.pk, message=request.POST.get('reason'))
            elif action == 'add_list':
                service.change_list_member(actor=request.user, list_id=request.POST.get('list_id'), participant_id=item.pk)
            elif action=='source_campaign':
                if not source_form.is_valid():raise ValidationError('Review the Campaign sourcing fields.')
                data=source_form.cleaned_data
                service.source_campaign(actor=request.user,campaign_id=data['campaign'].pk,participant_id=item.pk,exclusive=data['exclusive'],why=data['reason'])
            elif action=='release_source':
                from .models import CampaignSource
                from apps.participants.identity import identity_members
                source=get_object_or_404(CampaignSource,pk=request.POST.get('source'),participant_id__in=identity_members(item.pk))
                service.release_exclusivity(actor=request.user,source_id=source.pk,why=request.POST.get('reason'))
            elif action == 'review_selection':
                from .selection import opportunity_for
                opportunity = opportunity_for(request.user, request.POST.get('opportunity_id'), write=True)
                return redirect('recruiting:select-candidate', opportunity_id=opportunity.pk, participant_id=item.pk)
            elif action != 'preferences':
                raise ValidationError('Choose an available action.')
            else:
                raise ValidationError('Review the recruiting preference fields.')
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Recruiting information saved with attribution.')
            return redirect('recruiting:candidate', participant_id=item.pk)
    duplicates = participant_candidates(email=item.contact_email, phone=item.contact_phone).exclude(pk=item.pk)
    from .selection import opportunities_for
    from .models import CampaignSource
    from apps.participants.identity import identity_members
    from datetime import timedelta
    from django.utils import timezone
    visible=set(service.visible_campaigns(request.recruiting_actor).values_list('pk',flat=True))
    sources=[]
    for source in CampaignSource.objects.filter(participant_id__in=identity_members(item.pk)).select_related('campaign').order_by('-created_at'):
        policy=CampaignRecruitingPolicy.objects.filter(campaign=source.campaign).first()
        end=policy.completed_at+timedelta(days=policy.exclusivity_days) if policy and policy.completed_at and policy.exclusivity_days is not None else None
        status='Released' if source.released_at else 'Not exclusive' if not source.exclusive else 'Expired' if end and timezone.now()>=end else 'Active; completion/duration setup needed' if not end else 'Active'
        sources.append({'id':str(source.pk),'label':source.campaign.name if source.campaign_id in visible else 'Restricted Campaign sourcing',
            'status':status,'end':end if source.campaign_id in visible else None,'created_at':source.created_at,'released':source.released_at})
    return page(request, 'recruiting/candidate.html', participant=item, recruiting_profile=row, readiness=service.readiness(item),
        freshness=service.freshness(item), fingerprint=service.profile_fingerprint(item), form=form,
        candidate_lists=service.visible_lists(request.user).filter(owner=request.user, archived_at__isnull=True),
        possible_duplicates=duplicates[:20], available_opportunities=opportunities_for(request.user).filter(need__status='active',archived_at__isnull=True), page_title='Recruiting · ' + str(item), administrator=request.recruiting_actor.is_superuser,
        source_form=source_form,campaign_sources=sources)


@staff
@require_http_methods(['GET', 'POST'])
def consolidation(request, source_id, target_id):
    fresh_staff(request.user, administrator=True)
    try:
        comparison = service.consolidation_preview(source_id, target_id)
    except Participant.DoesNotExist:
        raise Http404
    form = ConsolidationForm(request.POST or None, initial={'source_id': source_id, 'target_id': target_id, 'fingerprint': comparison['fingerprint']})
    if request.method == 'POST' and form.is_valid():
        try:
            if form.cleaned_data['source_id'] != source_id or form.cleaned_data['target_id'] != target_id:
                raise ValidationError('The compared identities changed.')
            service.consolidate(actor=request.user, source_id=source_id, target_id=target_id,
                expected_fingerprint=form.cleaned_data['fingerprint'], confirmed=form.cleaned_data['confirmed'], why=form.cleaned_data['reason'])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Established identities linked; original records and facts retained.')
            return redirect('recruiting:candidate', participant_id=target_id)
    return page(request, 'recruiting/consolidation.html', comparison=comparison, form=form, page_title='Review established identities')


@staff
@require_http_methods(['GET', 'POST'])
def classifications(request, classification_id=None):
    fresh_staff(request.user, administrator=True)
    item = get_object_or_404(Classification, pk=classification_id) if classification_id else None
    form = ClassificationForm(request.POST or None, instance=item,initial={'expected_revision':item.updated_at.isoformat() if item else 'new'})
    if item:
        form.fields['kind'].disabled = True
        form.fields['key'].disabled = True
    if request.method == 'POST' and form.is_valid():
        try:
            with policy_transaction():
                actor = fresh_staff(request.user, administrator=True)
                current=Classification.objects.select_for_update().get(pk=item.pk) if item else None
                if form.cleaned_data['expected_revision']!=(current.updated_at.isoformat() if current else 'new'):
                    raise ValidationError('This classification changed. Reload its current settings.')
                fields=('kind','key','label','position','is_active')
                previous={k:getattr(current,k) for k in fields} if current else None
                result = form.save(commit=False);result.full_clean();result.save()
                service.audit(actor, 'classification.saved', result, data={k:getattr(result,k) for k in fields},before=previous,why=form.cleaned_data['reason'])
        except ValidationError as exc:form.add_error(None,exc)
        else:return redirect('recruiting:classifications')
    from apps.core.models import AuditLog
    from django.contrib.contenttypes.models import ContentType
    history=AuditLog.objects.filter(content_type=ContentType.objects.get_for_model(Classification),action='recruiting.classification.saved')
    if item:history=history.filter(object_id=str(item.pk))
    return page(request, 'recruiting/classifications.html', form=form, classifications=Classification.objects.all(),
        page_title='Employment & availability classifications', active_workspace='administration',history=history.order_by('-timestamp')[:30])


@staff
@require_http_methods(['GET', 'POST'])
def campaign_policy(request, campaign_id):
    from apps.customers.models import Campaign
    fresh_staff(request.user, administrator=True)
    campaign = get_object_or_404(Campaign, pk=campaign_id)
    row = CampaignRecruitingPolicy.objects.filter(campaign=campaign).first()
    form = CampaignPolicyForm(request.POST or None, initial={'days': row.exclusivity_days if row else None,
        'expected_revision': row.revision if row else 0, 'actual_completion': row.completed_at if row else None})
    if request.method == 'POST' and form.is_valid():
        try:
            service.configure_campaign(actor=request.user, campaign_id=campaign.pk, days=form.cleaned_data['days'],
                expected_revision=form.cleaned_data['expected_revision'], actual_completion=form.cleaned_data['actual_completion'], why=form.cleaned_data['reason'])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return redirect('recruiting:campaign-policy', campaign_id=campaign.pk)
    return page(request, 'recruiting/form.html', form=form, page_title=campaign.name + ' · Recruiting policy',
        description='Only an actual completed Campaign starts the configured exclusive period. No duration is assumed.')


from apps.participants.portal_views import participant_view


@participant_view
@require_http_methods(['GET', 'POST'])
def portal_preferences(request):
    item = request.portal_account.participant
    row = RecruitingProfile.objects.filter(participant=item).first()
    form = PreferencesForm(request.POST if request.method == 'POST' and request.POST.get('operation') == 'preferences' else None,
        initial={**initial_preferences(item),
                 'reason': 'Participant updated their recruiting preferences.'})
    if request.method == 'POST':
        if getattr(request, 'portal_preview', None):
            raise PermissionDenied('Return to Admin; recruiting confirmation requires the actual participant account.')
        try:
            if request.POST.get('operation') == 'confirm':
                service.confirm_profile(actor=request.user, participant_id=item.pk,
                    expected_fingerprint=request.POST.get('fingerprint'), participant_owned=True)
            elif request.POST.get('operation') == 'preferences' and form.is_valid():
                service.save_preferences(actor=request.user, participant_id=item.pk, data=form.cleaned_data,
                    expected_revision=form.cleaned_data['revision'], why=form.cleaned_data['reason'], participant_owned=True)
            else:
                raise ValidationError('Review the preference fields before saving.')
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Your information was recorded.')
            return redirect('participants:profile')
    return render(request, 'recruiting/portal_preferences.html', {'account': request.portal_account, 'section': 'profile',
        'form': form, 'fingerprint': service.profile_fingerprint(item), 'freshness': service.freshness(item),
        'update_requests': item.recruiting_update_requests.filter(fulfilled_by__isnull=True).order_by('-created_at')})
