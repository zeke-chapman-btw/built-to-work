from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from .auth import ParticipantAuthenticationBackend
from .forms import AccountRequestForm, EmailChangeForm, ParticipantLoginForm, ParticipantProfileForm, SetPasswordForm, StaffRequestResolutionForm
from .models import ParticipantAccount, ParticipantAccountRequest, ParticipantEmailChange
from .services import audit, begin_account_request, create_pending_account, find_participant_matches
from .tokens import VERIFY_SALT, SET_PASSWORD_SALT, EMAIL_CHANGE_SALT, make_token, read_token
from .normalization import normalize_email

def account_request(request):
    if request.method == "POST":
        form = AccountRequestForm(request.POST)
        if form.is_valid():
            begin_account_request(form.save(commit=False), request)
            return render(request, "participants/message.html", {"title": "Check your email", "message": "If the address can receive mail, a verification link has been sent."})
    else: form = AccountRequestForm()
    return render(request, "participants/form.html", {"title": "Request a participant account", "form": form})

def verify_request(request, token):
    key = read_token(token, VERIFY_SALT)
    if not key: return render(request, "participants/message.html", {"title": "Link expired", "message": "This verification link is invalid or expired. Submit a new request."}, status=400)
    obj = get_object_or_404(ParticipantAccountRequest, pk=key, status=ParticipantAccountRequest.Status.PENDING_VERIFICATION)
    obj.verified_at = timezone.now()
    matches = find_participant_matches(obj.contact_email, obj.contact_phone)
    obj.match_count = matches.count()
    obj.matched_participant = matches.first() if obj.match_count == 1 else None
    if obj.match_count == 1 and normalize_email(obj.matched_participant.contact_email) == obj.login_email:
        try:
            create_pending_account(obj.matched_participant, obj.login_email, obj.verified_at, request=request)
            obj.status = ParticipantAccountRequest.Status.LINKED
        except ValueError:
            obj.status = ParticipantAccountRequest.Status.PENDING_REVIEW
    else: obj.status = ParticipantAccountRequest.Status.PENDING_REVIEW
    obj.save(update_fields=["verified_at", "match_count", "matched_participant", "status", "updated_at"])
    audit(None, "participant_account_email_verified", obj, new_data={"status": obj.status, "match_count": obj.match_count})
    message = "Your request is verified and awaiting staff review." if obj.status == ParticipantAccountRequest.Status.PENDING_REVIEW else "Your identity was matched. Check your email for password setup."
    return render(request, "participants/message.html", {"title": "Email verified", "message": message})

def set_password(request, token):
    key = read_token(token, SET_PASSWORD_SALT)
    if not key: return render(request, "participants/message.html", {"title": "Link expired", "message": "This password setup link is invalid or expired."}, status=400)
    account = get_object_or_404(ParticipantAccount, pk=key, status=ParticipantAccount.Status.PENDING)
    if request.method == "POST":
        form = SetPasswordForm(request.POST)
        if form.is_valid():
            account.user.set_password(form.cleaned_data["password1"]); account.user.is_active = True; account.user.save(update_fields=["password", "is_active"])
            account.password_set_at = timezone.now(); account.status = ParticipantAccount.Status.ACTIVE; account.save(update_fields=["password_set_at", "status", "updated_at"])
            audit(account.user, "participant_account_activated", account, new_data={"status": account.status})
            return redirect("participants:login")
    else: form = SetPasswordForm()
    return render(request, "participants/form.html", {"title": "Set your password", "form": form})

def participant_login(request):
    if request.method == "POST":
        form = ParticipantLoginForm(request.POST)
        if form.is_valid():
            user = ParticipantAuthenticationBackend().authenticate(request, participant_email=form.cleaned_data["email"], password=form.cleaned_data["password"])
            if user is not None:
                auth_login(request, user, backend="apps.participants.auth.ParticipantAuthenticationBackend")
                return redirect("participants:profile")
            form.add_error(None, "Email or password is incorrect.")
    else: form = ParticipantLoginForm()
    return render(request, "participants/form.html", {"title": "Participant sign in", "form": form})

def participant_logout(request):
    if request.method == "POST": auth_logout(request)
    return redirect("participants:login")

def _account_for_user(user):
    try: return user.participant_account
    except ParticipantAccount.DoesNotExist: raise Http404

@login_required(login_url="participants:login")
def profile(request):
    account = _account_for_user(request.user)
    if account.status != ParticipantAccount.Status.ACTIVE: raise Http404
    participant = account.participant
    if request.method == "POST":
        form = ParticipantProfileForm(request.POST, instance=participant)
        if form.is_valid():
            before = {"first_name": participant.first_name, "last_name": participant.last_name, "preferred_name": participant.preferred_name, "contact_email": participant.contact_email, "contact_phone": participant.contact_phone}
            saved = form.save(); after = {f: getattr(saved, f) for f in before}
            audit(request.user, "participant_profile_updated", saved, old_data=before, new_data=after)
            messages.success(request, "Profile updated."); return redirect("participants:profile")
    else: form = ParticipantProfileForm(instance=participant)
    email_form = EmailChangeForm()
    return render(request, "participants/profile.html", {"form": form, "email_form": email_form, "account": account})

@login_required
def request_email_change(request):
    if request.method != "POST": return redirect("participants:profile")
    account = _account_for_user(request.user)
    form = EmailChangeForm(request.POST)
    if form.is_valid():
        email = form.cleaned_data["new_email"]
        if ParticipantAccount.objects.filter(status=ParticipantAccount.Status.ACTIVE, login_email__iexact=email).exclude(pk=account.pk).exists():
            form.add_error("new_email", "An active account already uses that email.")
        else:
            change, _ = ParticipantEmailChange.objects.update_or_create(account=account, defaults={"new_email": email, "verified_at": None})
            token = make_token(change.pk, EMAIL_CHANGE_SALT)
            url = request.build_absolute_uri(reverse("participants:verify-email-change", kwargs={"token": token}))
            send_mail("Verify your new participant account email", f"Verify within 24 hours: {url}", None, [email])
            return render(request, "participants/message.html", {"title": "Check your new email", "message": "A verification link has been sent. Your login email changes only after verification."})
    return render(request, "participants/form.html", {"title": "Change login email", "form": form})

def verify_email_change(request, token):
    key = read_token(token, EMAIL_CHANGE_SALT)
    if not key: return render(request, "participants/message.html", {"title": "Link expired", "message": "This email verification link is invalid or expired."}, status=400)
    change = get_object_or_404(ParticipantEmailChange, pk=key, verified_at__isnull=True)
    account = change.account
    if ParticipantAccount.objects.filter(status=ParticipantAccount.Status.ACTIVE, login_email__iexact=change.new_email).exclude(pk=account.pk).exists():
        return render(request, "participants/message.html", {"title": "Email unavailable", "message": "That email is now in use. Request a different address."}, status=409)
    old = account.login_email; account.login_email = change.new_email; account.user.email = change.new_email
    account.user.save(update_fields=["email"]); account.save(update_fields=["login_email", "updated_at"])
    change.verified_at = timezone.now(); change.save(update_fields=["verified_at", "updated_at"])
    audit(account.user, "participant_login_email_changed", account, old_data={"login_email": old}, new_data={"login_email": account.login_email})
    return render(request, "participants/message.html", {"title": "Email updated", "message": "Your participant sign-in email has been changed."})

def password_reset_request(request):
    if request.method == "POST":
        email = normalize_email(request.POST.get("email", ""))
        account = ParticipantAccount.objects.filter(login_email__iexact=email, status=ParticipantAccount.Status.ACTIVE, user__is_active=True).first()
        if account:
            token = make_token(account.user_id, SET_PASSWORD_SALT + ".reset")
            url = request.build_absolute_uri(reverse("participants:password-reset-confirm", kwargs={"token": token}))
            send_mail("Reset your participant password", f"Reset within 24 hours: {url}", None, [email])
        return render(request, "participants/message.html", {"title": "Check your email", "message": "If an active participant account matches, a reset link has been sent."})
    return render(request, "participants/form.html", {"title": "Reset password", "form": None})

def password_reset_confirm(request, token):
    key = read_token(token, SET_PASSWORD_SALT + ".reset")
    account = ParticipantAccount.objects.filter(user_id=key, status=ParticipantAccount.Status.ACTIVE).first() if key else None
    if not account: return render(request, "participants/message.html", {"title": "Link expired", "message": "This password reset link is invalid or expired."}, status=400)
    if request.method == "POST":
        form = SetPasswordForm(request.POST)
        if form.is_valid():
            account.user.set_password(form.cleaned_data["password1"]); account.user.save(update_fields=["password"])
            audit(account.user, "participant_password_reset", account, new_data={"status": account.status})
            return redirect("participants:login")
    else: form = SetPasswordForm()
    return render(request, "participants/form.html", {"title": "Choose a new password", "form": form})

@login_required(login_url="admin:login")
def staff_requests(request):
    if not request.user.is_staff: raise Http404
    items = ParticipantAccountRequest.objects.filter(status=ParticipantAccountRequest.Status.PENDING_REVIEW).order_by("created_at")
    return render(request, "internal/staff_requests.html", {"requests": items})

@login_required(login_url="admin:login")
@transaction.atomic
def staff_resolve_request(request, request_id):
    if not request.user.is_staff: raise Http404
    obj = get_object_or_404(ParticipantAccountRequest.objects.select_for_update(), pk=request_id, status=ParticipantAccountRequest.Status.PENDING_REVIEW, verified_at__isnull=False)
    if request.method == "POST":
        if request.POST.get("decision") == "reject":
            obj.status = ParticipantAccountRequest.Status.REJECTED; obj.resolved_by = request.user; obj.resolved_at = timezone.now(); obj.save(update_fields=["status", "resolved_by", "resolved_at", "updated_at"])
            audit(request.user, "participant_account_request_rejected", obj, new_data={"status": obj.status})
            return redirect("participants:staff-requests")
        form = StaffRequestResolutionForm(request.POST)
        if form.is_valid():
            participant = form.cleaned_data["participant"]
            create_pending_account(participant, obj.login_email, obj.verified_at, actor=request.user, request=request)
            obj.matched_participant = participant; obj.status = ParticipantAccountRequest.Status.LINKED; obj.resolved_by = request.user; obj.resolved_at = timezone.now(); obj.save(update_fields=["matched_participant", "status", "resolved_by", "resolved_at", "updated_at"])
            audit(request.user, "participant_account_request_resolved", obj, new_data={"participant_id": str(participant.pk), "status": obj.status})
            return redirect("participants:staff-requests")
    else: form = StaffRequestResolutionForm(initial={"participant": obj.matched_participant_id})
    return render(request, "internal/staff_request_resolve.html", {"title": f"Resolve request for {obj.login_email}", "form": form, "request_obj": obj})
