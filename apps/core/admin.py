from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "actor", "action", "content_type", "object_id", "source")
    list_filter = ("action", "source", "content_type")
    search_fields = ("action", "object_id", "reason")
    date_hierarchy = "timestamp"
    ordering = ("-timestamp",)
    readonly_fields = (
        "actor",
        "action",
        "content_type",
        "object_id",
        "timestamp",
        "source",
        "old_data",
        "new_data",
        "reason",
    )
    fields = readonly_fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
