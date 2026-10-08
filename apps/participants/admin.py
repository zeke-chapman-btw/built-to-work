from django.contrib import admin
from .models import Participant, ParticipantAccount, ParticipantAccountRequest, ParticipantEmailChange, RegistrationForm, RegistrationFormVersion, RegistrationQuestion, RegistrationSubmission, ConsentDocumentVersion, ConsentAcceptance

@admin.register(Participant)
class ParticipantAdmin(admin.ModelAdmin):
    list_display = ("last_name", "first_name", "kind", "contact_email", "contact_phone", "created_at")
    list_filter = ("kind",)
    search_fields = ("first_name", "last_name", "contact_email", "contact_phone", "identity_uuid")
    readonly_fields = ("id", "identity_uuid", "test_qr_token", "created_at", "updated_at", "archived_at")

@admin.register(ParticipantAccount)
class ParticipantAccountAdmin(admin.ModelAdmin):
    list_display = ("login_email", "participant", "status", "email_verified_at", "password_set_at")
    list_filter = ("status",)
    search_fields = ("login_email", "participant__first_name", "participant__last_name")
    readonly_fields = ("user", "participant", "login_email", "email_verified_at", "password_set_at", "created_at", "updated_at")

@admin.register(ParticipantAccountRequest)
class ParticipantAccountRequestAdmin(admin.ModelAdmin):
    list_display = ("login_email", "status", "match_count", "created_at", "verified_at")
    list_filter = ("status",)
    search_fields = ("login_email", "first_name", "last_name", "contact_email")
    readonly_fields = ("id", "first_name", "last_name", "preferred_name", "contact_email", "contact_phone", "login_email", "status", "matched_participant", "match_count", "verified_at", "resolved_by", "resolved_at", "created_at", "updated_at")

@admin.register(ParticipantEmailChange)
class ParticipantEmailChangeAdmin(admin.ModelAdmin):
    list_display = ("account", "new_email", "verified_at", "created_at")
    readonly_fields = ("account", "new_email", "verified_at", "created_at", "updated_at")
from django.contrib import admin
from .models import RegistrationForm, RegistrationFormVersion, RegistrationQuestion, RegistrationSubmission, ConsentDocumentVersion, ConsentAcceptance

@admin.register(RegistrationForm)
class RegistrationFormAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "updated_at")
    search_fields = ("name", "slug")

@admin.register(RegistrationFormVersion)
class RegistrationFormVersionAdmin(admin.ModelAdmin):
    list_display = ("form", "version", "status", "published_at")
    list_filter = ("status",)
    readonly_fields = ("snapshot",)

@admin.register(RegistrationQuestion)
class RegistrationQuestionAdmin(admin.ModelAdmin):
    list_display = ("label", "canonical_key", "field_type", "is_active", "position")
    list_filter = ("field_type", "is_active", "is_protected")
    search_fields = ("label", "canonical_key")

@admin.register(RegistrationSubmission)
class RegistrationSubmissionAdmin(admin.ModelAdmin):
    list_display = ("id", "participant", "event", "status", "submitted_at")
    list_filter = ("status",)
    readonly_fields = ("answers", "answer_snapshot")

@admin.register(ConsentDocumentVersion)
class ConsentDocumentVersionAdmin(admin.ModelAdmin):
    list_display = ("key", "version", "is_approved", "effective_at")

@admin.register(ConsentAcceptance)
class ConsentAcceptanceAdmin(admin.ModelAdmin):
    list_display = ("participant", "document", "accepted_at")
    readonly_fields = ("accepted_at",)
