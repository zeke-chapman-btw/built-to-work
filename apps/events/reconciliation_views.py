"""Existing reversible identity review, using the shared internal presentation."""
from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.shortcuts import get_object_or_404,redirect,render
from django.views.decorators.http import require_POST
from apps.operations.presentation import list_page
from .models import ParticipantIdentityLink,ParticipantMatchReview
from .reconciliation import resolve_match,reverse_match

class DecisionForm(forms.Form):
    decision=forms.ChoiceField(choices=(("same","Same person — link identities"),("separate","Different people — keep separate")))
    reason=forms.CharField(max_length=500,widget=forms.Textarea(attrs={"rows":3}))
class ReverseForm(forms.Form):
    reason=forms.CharField(max_length=500,label="Reversal reason",widget=forms.Textarea(attrs={"rows":3}))

def _can_view(user):
    return user.is_authenticated and user.is_active and user.is_staff and user.has_perm("events.view_participantmatchreview")

def review_object(review_id):
    return get_object_or_404(ParticipantMatchReview.objects.select_related("envelope__preparation__event","candidate_participant"),pk=review_id)

def detail(request,review,decision_form=None,reverse_form=None,status=200):
    return render(request,"events/match_review_detail.html",dict(page_title="Participant match review",review=review,
        source=review.envelope.payload.get("participant") or {},
        history=review.decisions.select_related("actor").order_by("created_at","pk"),
        can_resolve=request.user.has_perm("events.change_participantmatchreview"),
        can_reverse=request.user.is_superuser,link=ParticipantIdentityLink.objects.filter(review=review).first(),
        decision_form=decision_form if decision_form is not None else DecisionForm(),
        reverse_form=reverse_form if reverse_form is not None else ReverseForm()),status=status)

@login_required(login_url="/admin/login/")
def match_review_list(request):
    if not _can_view(request.user):raise PermissionDenied
    reviews,filters=list_page(request,ParticipantMatchReview.objects.select_related("envelope__preparation__event","candidate_participant").order_by("status","-envelope__created_at","pk"),
        search_fields=("envelope__preparation__event__name","candidate_participant__first_name","candidate_participant__last_name"),
        name_field="candidate_participant__first_name",newest_field="-envelope__created_at",
        status_field="status",status_choices=ParticipantMatchReview.Status.choices)
    return render(request,"events/match_review_list.html",dict(page_title="Participant match reviews",reviews=reviews,**filters))

@login_required(login_url="/admin/login/")
def match_review_detail(request,review_id):
    if not _can_view(request.user):raise PermissionDenied
    return detail(request,review_object(review_id))

@login_required(login_url="/admin/login/")
@require_POST
def match_review_resolve(request,review_id):
    if not _can_view(request.user) or not request.user.has_perm("events.change_participantmatchreview"):raise PermissionDenied
    review=review_object(review_id);form=DecisionForm(request.POST)
    if form.is_valid():
        try:resolve_match(review_id=review_id,action=form.cleaned_data["decision"],reason=form.cleaned_data["reason"],actor=request.user)
        except ValidationError as exc:form.add_error(None,exc)
        else:return redirect("events:match-review-detail",review_id=review_id)
    return detail(request,review,decision_form=form,status=400)

@login_required(login_url="/admin/login/")
@require_POST
def match_review_reverse(request,review_id):
    if not request.user.is_active or not request.user.is_staff or not request.user.is_superuser:raise PermissionDenied
    review=review_object(review_id);form=ReverseForm(request.POST)
    if form.is_valid():
        try:reverse_match(review_id=review_id,reason=form.cleaned_data["reason"],actor=request.user)
        except ValidationError as exc:form.add_error(None,exc)
        else:return redirect("events:match-review-detail",review_id=review_id)
    return detail(request,review,reverse_form=form,status=400)
