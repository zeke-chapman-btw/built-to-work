"""Raw Django Admin cannot bypass the Operations intake lifecycle or DOB policy."""
from django.core.exceptions import PermissionDenied


def full_dob_allowed(user):
    return bool(user.is_authenticated and user.is_active and user.is_staff
                and (user.is_superuser or user.has_perm("participants.view_participant_dob")))


class IntakeConfigAdminBoundary:
    intake_form_lookup = "form__is_smart_intake"

    def _smart(self, obj):
        return obj.is_smart_intake if self.intake_form_lookup == "is_smart_intake" else obj.form.is_smart_intake

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.exclude(**{self.intake_form_lookup: True})

    def has_view_permission(self, request, obj=None):
        if obj is not None and self._smart(obj):
            return request.user.is_active and request.user.is_superuser
        return super().has_view_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if obj is not None and self._smart(obj):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and self._smart(obj):
            return False
        return super().has_delete_permission(request, obj)

    def delete_queryset(self, request, queryset):
        if queryset.filter(**{self.intake_form_lookup: True}).exists():
            raise PermissionDenied("Retain intake history through the Operations lifecycle.")
        return super().delete_queryset(request, queryset)

    def get_readonly_fields(self, request, obj=None):
        fields = tuple(super().get_readonly_fields(request, obj))
        return fields + ("is_smart_intake",) if self.intake_form_lookup == "is_smart_intake" else fields

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "form":
            from .models import RegistrationForm
            kwargs["queryset"] = RegistrationForm.objects.filter(is_smart_intake=False)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class SubmissionPrivacyBoundary:
    def has_view_permission(self, request, obj=None):
        return full_dob_allowed(request.user) and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return full_dob_allowed(request.user) and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.intake_operation_id:
            return False
        return full_dob_allowed(request.user) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.intake_operation_id:
            return False
        return full_dob_allowed(request.user) and super().has_delete_permission(request, obj)

    def delete_queryset(self, request, queryset):
        if queryset.filter(intake_operation_id__isnull=False).exists():
            raise PermissionDenied("Submitted intake evidence must be retained.")
        return super().delete_queryset(request, queryset)
