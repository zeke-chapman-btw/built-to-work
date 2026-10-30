from django.contrib import admin
from django.db.models import Q

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

    def get_queryset(self, request):
        query = super().get_queryset(request)
        from apps.customers.access import is_manager
        if not is_manager(request.user):
            # A legacy audit grant must not bypass the customer role boundary.
            # Keep unrelated audit records and their existing permissions intact.
            query = query.exclude(Q(customer_scopes__isnull=False)
                                  | Q(content_type__app_label="customers")
                                  | Q(source="customer_management")
                                  | Q(action__startswith="customer."))
        return query

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
