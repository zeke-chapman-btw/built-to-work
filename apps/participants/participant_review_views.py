import uuid
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from apps.core.audit import record_audit
from .models import Participant
from .participant_review_models import ParticipantReviewRecord


def can_view_participant_reviews(user):
    return bool(user.is_authenticated and user.is_active and user.is_staff and user.has_perm("participants.view_participant") and (user.has_perm("participants.view_participantreviewrecord") or user.has_perm("participants.review_participants")))


def can_review_participants(user):
    return can_view_participant_reviews(user) and user.has_perm("participants.review_participants")


class ParticipantReviewForm(forms.Form):
    request_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)
    previous_record = forms.IntegerField(required=False, widget=forms.HiddenInput)
    status = forms.ChoiceField(choices=ParticipantReviewRecord.Status.choices)
    internal_notes = forms.CharField(max_length=4000, widget=forms.Textarea(attrs={"rows": 4}), label="Internal review notes")


@login_required
@require_http_methods(["GET", "POST"])
def participant_review(request, participant_id):
    if not can_view_participant_reviews(request.user):
        raise PermissionDenied
    participant = get_object_or_404(Participant, pk=participant_id)
    history = participant.internal_reviews.select_related("reviewer")
    latest = history.first()
    form = ParticipantReviewForm(request.POST if request.method == "POST" else None, initial={"previous_record": latest.pk if latest else None})
    if request.method == "POST":
        if not can_review_participants(request.user):
            raise PermissionDenied
        if form.is_valid():
            with transaction.atomic():
                Participant.objects.select_for_update().get(pk=participant.pk)
                prior = participant.internal_reviews.first()
                existing = ParticipantReviewRecord.objects.filter(request_key=form.cleaned_data["request_key"]).first()
                if existing:
                    if existing.participant_id != participant.pk or existing.reviewer_id != request.user.pk or existing.status != form.cleaned_data["status"] or existing.internal_notes != form.cleaned_data["internal_notes"]:
                        raise PermissionDenied
                    messages.success(request, "This internal review was already recorded.")
                    return redirect("participants:staff-participant-review", participant_id=participant.pk)
                if form.cleaned_data["previous_record"] != (prior.pk if prior else None):
                    form.add_error(None, "Another reviewer updated this participant. Reload before recording your decision.")
                else:
                    record = ParticipantReviewRecord.objects.create(participant=participant, status=form.cleaned_data["status"], internal_notes=form.cleaned_data["internal_notes"], reviewer=request.user, supersedes=prior, request_key=form.cleaned_data["request_key"])
                    record_audit(actor=request.user, action="participant.internal_review.recorded", instance=participant, source="staff_interface", old_data={"status": prior.status if prior else None}, new_data={"status": record.status, "review_record_id": record.pk}, reason="Internal participant review recorded")
                    messages.success(request, "Internal participant review recorded.")
                    return redirect("participants:staff-profile", participant_id=participant.pk)
    return render(request, "participants/staff_participant_review.html", {"participant": participant, "history": history, "latest": latest, "form": form, "can_decide": can_review_participants(request.user), "page_title": "Internal participant review"}, status=400 if form.is_bound and form.errors else 200)
