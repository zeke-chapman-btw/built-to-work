from io import BytesIO
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from PIL import Image

from .models import OfflineEventPreparation, OfflineRegistrationEnvelope, EventRegistration
from .offline import check_offline_readiness, activate_offline, create_offline_registration
from .ticket_renderer import ticket_png_bytes
from .views import staff_required


class OfflineRegistrationForm(forms.Form):
    first_name = forms.CharField(max_length=120, label="First name")
    last_name = forms.CharField(max_length=120, label="Last name")
    contact_email = forms.EmailField(required=False, label="Email")
    contact_phone = forms.CharField(max_length=40, required=False, label="Phone")
    consent_ack = forms.BooleanField(required=True, label="I agree to the approved consent document")

    def __init__(self, *args, preparation=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.preparation = preparation
        groups = preparation.event.groups.filter(is_active=True).order_by("name")
        self.fields["group"] = forms.ChoiceField(
            required=preparation.event.group_mode == "required",
            choices=[("", "No group")] + [(str(group.pk), group.name) for group in groups],
            label="Group",
        )
        self.question_definitions = list(
            preparation.event.registration_questions.filter(is_active=True).order_by("position", "created_at")
        )
        for question in self.question_definitions:
            options = question.options if isinstance(question.options, list) else []
            choices = [(str(option.get("value", option.get("label", ""))), str(option.get("label", option.get("value", "")))) if isinstance(option, dict) else (str(option), str(option)) for option in options]
            kwargs = {"label": question.label, "required": question.is_required, "help_text": question.help_text if hasattr(question, "help_text") else ""}
            if question.field_type in {"single_choice", "yes_no"}:
                if question.field_type == "yes_no":
                    choices = [("yes", "Yes"), ("no", "No")]
                self.fields[question.key] = forms.ChoiceField(choices=choices, **kwargs)
            elif question.field_type == "long_text":
                self.fields[question.key] = forms.CharField(widget=forms.Textarea, **kwargs)
            else:
                self.fields[question.key] = forms.CharField(**kwargs)


@staff_required
def offline_preparations(request):
    preparations = OfflineEventPreparation.objects.select_related("event", "trailer").order_by("event__starts_at")
    return render(request, "events/offline_preparations.html", {"preparations": preparations})


@staff_required
def offline_preparation_detail(request, preparation_id):
    preparation = get_object_or_404(OfflineEventPreparation.objects.select_related("event", "trailer", "consent_document"), pk=preparation_id)
    readiness = check_offline_readiness(preparation)
    return render(request, "events/offline_preparation_detail.html", {"preparation": preparation, "readiness": readiness})


@staff_required
def offline_activate(request, preparation_id):
    preparation = get_object_or_404(OfflineEventPreparation, pk=preparation_id)
    if request.method != "POST":
        return redirect("events:offline-preparation-detail", preparation_id=preparation.pk)
    try:
        activate_offline(preparation=preparation, actor=request.user)
        messages.success(request, "Offline registration is active.")
    except ValidationError as exc:
        messages.error(request, str(exc))
    return redirect("events:offline-preparation-detail", preparation_id=preparation.pk)


@staff_required
def offline_register(request, preparation_id):
    preparation = get_object_or_404(OfflineEventPreparation.objects.select_related("event", "consent_document"), pk=preparation_id)
    form = OfflineRegistrationForm(request.POST or None, preparation=preparation)
    if request.method == "POST" and form.is_valid():
        answers = {question.key: form.cleaned_data.get(question.key) for question in form.question_definitions}
        data = dict(form.cleaned_data)
        data["answers"] = answers
        try:
            _, registration, ticket, envelope = create_offline_registration(preparation=preparation, actor=request.user, data=data)
            return redirect("events:offline-ticket-png", envelope_id=envelope.pk)
        except ValidationError as exc:
            form.add_error(None, str(exc))
    return render(request, "events/offline_register.html", {"preparation": preparation, "form": form})


@staff_required
def offline_ticket_png(request, envelope_id):
    envelope = get_object_or_404(OfflineRegistrationEnvelope, pk=envelope_id)
    registration = get_object_or_404(EventRegistration, pk=envelope.registration_id)
    ticket = registration.qr_tickets.filter(is_current=True).first()
    if ticket is None:
        return HttpResponse("Ticket unavailable", status=404)
    response = HttpResponse(ticket_png_bytes(ticket), content_type="image/png")
    response["Content-Disposition"] = f"attachment; filename=BTW-ticket-{ticket.ticket_number}.png"
    return response


@staff_required
def offline_print(request, envelope_id):
    envelope = get_object_or_404(OfflineRegistrationEnvelope, pk=envelope_id)
    messages.info(request, "Ticket is ready for local printing or PDF download.")
    return redirect("events:offline-ticket-png", envelope_id=envelope.pk)


@staff_required
def offline_ticket_pdf(request, envelope_id):
    envelope = get_object_or_404(OfflineRegistrationEnvelope, pk=envelope_id)
    registration = get_object_or_404(EventRegistration, pk=envelope.registration_id)
    ticket = registration.qr_tickets.filter(is_current=True).first()
    if ticket is None:
        raise Http404("Ticket is unavailable")
    image = Image.open(BytesIO(ticket_png_bytes(ticket))).convert("RGB")
    output = BytesIO()
    image.save(output, format="PDF")
    response = HttpResponse(output.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f"attachment; filename=BTW-ticket-{ticket.ticket_number}.pdf"
    return response
