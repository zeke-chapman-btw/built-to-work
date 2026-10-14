from django.contrib import admin

from .models import Attendance, Event, EventGroup, EventRegistration, EventRegistrationQuestion, QrTicket


class EventGroupInline(admin.TabularInline):
    model = EventGroup
    extra = 0
    fields = ("name", "is_active", "capacity", "registration_deadline_at")


class EventQuestionInline(admin.TabularInline):
    model = EventRegistrationQuestion
    extra = 0
    fields = ("key", "label", "field_type", "options", "is_required", "is_active", "position")


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    inlines = (EventGroupInline, EventQuestionInline)
    list_display = ("name", "code", "start_at", "end_at", "status", "created_at")
    list_filter = ("status", "timezone_name")
    search_fields = ("name", "code", "location_name", "city")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(EventRegistration)
class EventRegistrationAdmin(admin.ModelAdmin):
    list_display = ("event", "participant", "status", "source", "registered_at")
    list_filter = ("status", "source", "registered_at")
    search_fields = ("event__name", "participant__first_name", "participant__last_name", "participant__contact_email")
    readonly_fields = ("id", "registered_at", "created_at", "updated_at", "group", "custom_answers")


@admin.register(QrTicket)
class QrTicketAdmin(admin.ModelAdmin):
    list_display = ("id", "registration", "is_current", "issued_at", "expires_at", "revoked_at")
    list_filter = ("is_current", "issued_at", "expires_at", "revoked_at")
    search_fields = ("registration__event__name", "registration__participant__first_name", "registration__participant__last_name")
    readonly_fields = ("id", "issued_at", "revoked_at", "superseded_by")
    exclude = ("token",)


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ("event", "participant", "checked_in_at", "source", "is_void")
    list_filter = ("source", "is_void", "checked_in_at")
    search_fields = ("event__name", "participant__first_name", "participant__last_name", "participant__contact_email")
    readonly_fields = ("id", "checked_in_at", "created_at", "updated_at", "voided_at")


@admin.register(EventGroup)
class EventGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "event", "is_active", "capacity", "registration_count", "registration_deadline_at")
    list_filter = ("is_active", "event")
    search_fields = ("name", "event__name")


@admin.register(EventRegistrationQuestion)
class EventRegistrationQuestionAdmin(admin.ModelAdmin):
    list_display = ("label", "event", "key", "is_required", "is_active", "position")
    list_filter = ("event", "is_required", "is_active")
    search_fields = ("label", "key", "event__name")


# Build 7C-4C operational records are read-only in Django Admin.
from django.contrib import admin as operational_admin
from .models import BackupRecord, ParticipantMatchReview, TicketDelivery


@operational_admin.register(BackupRecord)
class BackupRecordAdmin(operational_admin.ModelAdmin):
    list_display = ("trailer", "status", "created_at", "completed_at")
    list_filter = ("status", "trailer")
    readonly_fields = tuple(field.name for field in BackupRecord._meta.fields)
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return bool(obj and super().has_view_permission(request, obj))

    def has_delete_permission(self, request, obj=None):
        return False


@operational_admin.register(TicketDelivery)
class TicketDeliveryAdmin(operational_admin.ModelAdmin):
    list_display = ("registration", "channel", "status", "attempts", "created_at", "sent_at")
    list_filter = ("channel", "status")
    readonly_fields = tuple(field.name for field in TicketDelivery._meta.fields)
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return bool(obj and super().has_view_permission(request, obj))

    def has_delete_permission(self, request, obj=None):
        return False


@operational_admin.register(ParticipantMatchReview)
class ParticipantMatchReviewAdmin(operational_admin.ModelAdmin):
    list_display = ("envelope", "candidate_participant", "status", "reviewed_by", "reviewed_at")
    list_filter = ("status",)
    readonly_fields = tuple(field.name for field in ParticipantMatchReview._meta.fields)
    ordering = ("status", "id")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return bool(obj and super().has_view_permission(request, obj))

    def has_delete_permission(self, request, obj=None):
        return False


from .models import OperationalIncident, OperationalNotificationDelivery, OperationalNotificationRule

@operational_admin.register(OperationalNotificationRule)
class OperationalNotificationRuleAdmin(operational_admin.ModelAdmin):
    list_display = ("kind", "event", "enabled", "updated_at")
    list_filter = ("kind", "enabled")
    fields = ("event", "kind", "enabled", "channels", "recipients")

@operational_admin.register(OperationalIncident)
class OperationalIncidentAdmin(operational_admin.ModelAdmin):
    list_display = ("kind", "event", "status", "occurrences", "last_seen_at")
    list_filter = ("kind", "status")
    readonly_fields = ("fingerprint", "event", "kind", "summary", "occurrences", "created_at", "last_seen_at", "resolved_at")
    fields = ("status",) + readonly_fields

    def has_add_permission(self, request):
        return False

    def save_model(self, request, obj, form, change):
        from django.utils import timezone
        obj.resolved_at = timezone.now() if obj.status == OperationalIncident.Status.RESOLVED else None
        super().save_model(request, obj, form, change)

@operational_admin.register(OperationalNotificationDelivery)
class OperationalNotificationDeliveryAdmin(operational_admin.ModelAdmin):
    list_display = ("incident", "channel", "status", "attempts", "created_at", "sent_at")
    list_filter = ("channel", "status")
    readonly_fields = tuple(field.name for field in OperationalNotificationDelivery._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


# Build 7F preparation is an authorized central Admin operation. Credentials are
# accepted once, hashed immediately, and never rendered again.
from django import forms
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from .models import TrailerInstance, OfflineEventPreparation, OfflineTicketRange, CentralEventDownload, TicketAllocationBlock
from django.forms.models import BaseInlineFormSet


class TrailerCredentialForm(forms.ModelForm):
    new_credential = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False),
                                     help_text="Set a new per-trailer credential to provision or rotate access.")

    class Meta:
        model = TrailerInstance
        fields = ("identity", "display_name", "is_active", "credential_revoked_at")

    def clean(self):
        cleaned = super().clean()
        if not self.instance.pk and not cleaned.get("new_credential"):
            raise ValidationError("A credential is required for a new trailer.")
        return cleaned


@admin.register(TrailerInstance)
class TrailerInstanceAdmin(admin.ModelAdmin):
    form = TrailerCredentialForm
    list_display = ("identity", "display_name", "is_active", "credential_rotated_at", "credential_revoked_at")
    readonly_fields = ("credential_rotated_at", "created_at")

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        credential = form.cleaned_data.get("new_credential")
        if credential:
            from django.utils import timezone
            obj.credential_hash = make_password(credential)
            obj.credential_rotated_at = timezone.now()
            obj.credential_revoked_at = None
        super().save_model(request, obj, form, change)


class OfflineTicketRangeFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        spans = []
        kinds = set()
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            data = form.cleaned_data
            kind, start, end = data.get("kind"), data.get("start_number"), data.get("end_number")
            if kind is None or start is None or end is None:
                continue
            if kind in kinds:
                raise ValidationError("Only one primary and one backup range are permitted.")
            kinds.add(kind)
            spans.append((start, end))
            if TicketAllocationBlock.objects.filter(start_number__lte=end, end_number__gte=start).exists():
                raise ValidationError("A trailer range overlaps an online ticket allocation block.")
            if OfflineTicketRange.objects.exclude(preparation_id=self.instance.pk).filter(start_number__lte=end, end_number__gte=start).exists():
                raise ValidationError("A trailer range overlaps another trailer allocation.")
        if kinds != {"primary", "backup"}:
            raise ValidationError("Both a primary and backup range are required.")
        if len(spans) == 2 and spans[0][0] <= spans[1][1] and spans[1][0] <= spans[0][1]:
            raise ValidationError("Primary and backup ticket ranges cannot overlap.")


class OfflineTicketRangeInline(admin.TabularInline):
    model = OfflineTicketRange
    formset = OfflineTicketRangeFormSet
    extra = 2
    fields = ("kind", "start_number", "end_number", "next_number", "is_exhausted")


class OfflineEventPreparationForm(forms.ModelForm):
    class Meta:
        model = OfflineEventPreparation
        fields = ("event", "trailer", "status", "consent_document")

    def clean(self):
        data = super().clean()
        event = data.get("event")
        doc = data.get("consent_document") or (event.public_consent_document if event else None)
        if not doc or not doc.is_approved or not doc.original_pdf:
            raise ValidationError("An approved consent PDF is required to prepare an event.")
        return data


@admin.register(OfflineEventPreparation)
class OfflineEventPreparationAdmin(admin.ModelAdmin):
    form = OfflineEventPreparationForm
    list_display = ("event", "trailer", "status", "updated_at")
    list_filter = ("status", "trailer")
    fields = ("event", "trailer", "status", "consent_document")
    inlines = (OfflineTicketRangeInline,)

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not obj.consent_document_id:
            obj.consent_document = obj.event.public_consent_document
        obj.consent_hash = obj.consent_document.content_hash
        obj.sync_identity = obj.trailer.identity
        super().save_model(request, obj, form, change)


@admin.register(CentralEventDownload)
class CentralEventDownloadAdmin(admin.ModelAdmin):
    list_display = ("event_id", "trailer_identity", "status", "downloaded_count", "last_success_at", "last_error")
    readonly_fields = tuple(field.name for field in CentralEventDownload._meta.fields)

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
