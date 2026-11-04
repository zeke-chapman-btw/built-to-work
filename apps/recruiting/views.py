from functools import wraps
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
from .models import CandidateList, RecruitingProfile, Classification, CampaignRecruitingPolicy
from .forms import PreferencesForm, ListForm, ConsolidationForm, ClassificationForm, CampaignPolicyForm
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
    initial = {'revision': row.revision if row else 0,
        'employment_classification': row.employment_classification_id if row else None,
        'availability_classification': row.availability_classification_id if row else None,
        'willing_to_relocate': '' if not row or row.willing_to_relocate is None else 'yes' if row.willing_to_relocate else 'no'}
    form = PreferencesForm(request.POST if request.method == 'POST' and request.POST.get('operation') == 'preferences' else None, initial=initial)
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
    return page(request, 'recruiting/candidate.html', participant=item, recruiting_profile=row, readiness=service.readiness(item),
        freshness=service.freshness(item), fingerprint=service.profile_fingerprint(item), form=form,
        candidate_lists=service.visible_lists(request.user).filter(owner=request.user, archived_at__isnull=True),
        possible_duplicates=duplicates[:20], page_title='Recruiting · ' + str(item), administrator=request.recruiting_actor.is_superuser)


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
    form = ClassificationForm(request.POST or None, instance=item)
    if item:
        form.fields['kind'].disabled = True
        form.fields['key'].disabled = True
    if request.method == 'POST' and form.is_valid():
        with policy_transaction():
            actor = fresh_staff(request.user, administrator=True)
            result = form.save()
            service.audit(actor, 'classification.saved', result, data={'key': result.key, 'active': result.is_active})
        return redirect('recruiting:classifications')
    return page(request, 'recruiting/classifications.html', form=form, classifications=Classification.objects.all(),
        page_title='Employment & availability classifications', active_workspace='administration')


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
        initial={'revision': row.revision if row else 0,
                 'employment_classification': row.employment_classification_id if row else None,
                 'availability_classification': row.availability_classification_id if row else None,
                 'willing_to_relocate': '' if not row or row.willing_to_relocate is None else 'yes' if row.willing_to_relocate else 'no',
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
