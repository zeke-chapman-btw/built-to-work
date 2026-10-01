from django.contrib import admin

from .models import EventStation, ExperienceActivity, ExperienceSession, Station


class EventStationInline(admin.TabularInline):
    model = EventStation
    extra = 0


@admin.register(Station)
class StationAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "station_type", "location", "is_active")
    list_filter = ("station_type", "is_active")
    search_fields = ("code", "name", "location")
    inlines = (EventStationInline,)


@admin.register(EventStation)
class EventStationAdmin(admin.ModelAdmin):
    list_display = ("event", "station", "enabled", "is_active_context", "display_order")
    list_filter = ("enabled", "is_active_context", "station__station_type")
    search_fields = ("event__name", "station__name", "station__code")


class ExperienceActivityInline(admin.TabularInline):
    model = ExperienceActivity
    extra = 0
    readonly_fields = ("status", "started_at", "completed_at", "skipped_at", "skipped_by", "skip_reason")


@admin.register(ExperienceSession)
class ExperienceSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "event", "mode", "registration", "started_at", "completed_at")
    list_filter = ("mode", "event")
    readonly_fields = ("started_at", "completed_at")
    inlines = (ExperienceActivityInline,)


@admin.register(ExperienceActivity)
class ExperienceActivityAdmin(admin.ModelAdmin):
    list_display = ("session", "activity", "status", "started_at", "completed_at", "skipped_by")
    list_filter = ("activity", "status")
    readonly_fields = ("session", "activity", "status", "started_at", "completed_at", "skipped_at", "skipped_by", "skip_reason")
