"""Customer writes belong to the versioned workspace services, not raw admin forms."""
from django.contrib import admin

from .access import is_manager, require_manager
from .models import (CustomerOrganization, CustomerLocation, CustomerContact,
                     CustomerRelationship, CustomerIdentity, CustomerMembership,
                     CustomerAccessPlan, WorkforceNeed, WorkforceNeedRevision,
                     Campaign, CustomerAuditScope)


class CustomerReadOnlyAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return is_manager(request.user)

    def has_view_permission(self, request, obj=None):
        return is_manager(request.user)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        require_manager(request.user)
        return super().get_queryset(request)


for model in (CustomerOrganization, CustomerLocation, CustomerContact,
              CustomerRelationship, CustomerIdentity, CustomerMembership,
              CustomerAccessPlan, WorkforceNeed, WorkforceNeedRevision,
              Campaign, CustomerAuditScope):
    admin.site.register(model, CustomerReadOnlyAdmin)
