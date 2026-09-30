from django.contrib import admin
from .models import Participant, ParticipantAccount, ParticipantAccountRequest, ParticipantEmailChange

@admin.register(Participant)
class ParticipantAdmin(admin.ModelAdmin):
    list_display = ("last_name", "first_name", "contact_email", "contact_phone", "created_at")
    search_fields = ("first_name", "last_name", "contact_email", "contact_phone")
    readonly_fields = ("id", "created_at", "updated_at", "archived_at")

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
