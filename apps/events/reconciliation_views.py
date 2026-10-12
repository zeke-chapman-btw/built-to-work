"""Permission-checked staff screens for uncertain participant matches."""
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import ParticipantIdentityLink, ParticipantMatchReview
from .reconciliation import resolve_match, reverse_match


def _can_view(user):
    return (user.is_authenticated and user.is_active and user.is_staff
            and user.has_perm("events.view_participantmatchreview"))


@login_required(login_url="/admin/login/")
def match_review_list(request):
    if not _can_view(request.user):
        raise PermissionDenied
    reviews = ParticipantMatchReview.objects.select_related(
        "envelope__preparation__event", "candidate_participant"
    ).order_by("status", "-envelope__created_at")[:200]
    return render(request, "events/match_review_list.html", {"reviews": reviews})


@login_required(login_url="/admin/login/")
def match_review_detail(request, review_id):
    if not _can_view(request.user):
        raise PermissionDenied
    review = get_object_or_404(
        ParticipantMatchReview.objects.select_related(
            "envelope__preparation__event", "candidate_participant"
        ), pk=review_id,
    )
    source = review.envelope.payload.get("participant") or {}
    return render(request, "events/match_review_detail.html", {
        "review": review, "source": source,
        "history": review.decisions.select_related("actor").order_by("created_at", "pk"),
        "can_resolve": request.user.has_perm("events.change_participantmatchreview"),
        "can_reverse": request.user.is_superuser,
        "link": ParticipantIdentityLink.objects.filter(review=review).first(),
    })


@login_required(login_url="/admin/login/")
@require_POST
def match_review_resolve(request, review_id):
    if not _can_view(request.user) or not request.user.has_perm("events.change_participantmatchreview"):
        raise PermissionDenied
    get_object_or_404(ParticipantMatchReview, pk=review_id)
    try:
        resolve_match(
            review_id=review_id, action=request.POST.get("decision"),
            reason=request.POST.get("reason"), actor=request.user,
        )
    except ValidationError as exc:
        return render(request, "events/match_review_error.html", {"error": exc.messages[0]}, status=400)
    return redirect("events:match-review-detail", review_id=review_id)


@login_required(login_url="/admin/login/")
@require_POST
def match_review_reverse(request, review_id):
    if not request.user.is_active or not request.user.is_superuser:
        raise PermissionDenied
    get_object_or_404(ParticipantMatchReview, pk=review_id)
    try:
        reverse_match(review_id=review_id, reason=request.POST.get("reason"), actor=request.user)
    except ValidationError as exc:
        return render(request, "events/match_review_error.html", {"error": exc.messages[0]}, status=400)
    return redirect("events:match-review-detail", review_id=review_id)
