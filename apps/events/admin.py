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
