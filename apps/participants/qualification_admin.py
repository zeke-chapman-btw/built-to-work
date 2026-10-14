"""Permission-checked, audited Django Admin workflows for qualifications."""
from django.contrib import admin
from django.forms.models import model_to_dict
from apps.core.audit import record_audit
from apps.participants.models import (
    Participant, ParticipantQualification, QualificationCategory,
    QualificationCatalogEntry, RegistrationQualificationMapping,
)


def _has(request, codename):
    return bool(request.user.is_superuser or request.user.has_perm(f"participants.{codename}"))


def _snapshot(obj):
    return {k: str(v) if v is not None else None for k, v in model_to_dict(obj).items()}


class PermissionedAuditAdmin(admin.ModelAdmin):
    permission_codename = ""
    audit_prefix = "qualification"
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")

    def has_module_permission(self, request):
        return _has(request, self.permission_codename)

    def has_view_permission(self, request, obj=None):
        return _has(request, self.permission_codename)

    def has_add_permission(self, request):
        return _has(request, self.permission_codename)

    def has_change_permission(self, request, obj=None):
        return _has(request, self.permission_codename)

    def has_delete_permission(self, request, obj=None):
        return False  # Configuration and history are deactivated, never deleted.

    def save_model(self, request, obj, form, change):
        old_data = _snapshot(self.model.objects.get(pk=obj.pk)) if change else {}
        if not change and hasattr(obj, "created_by_id"):
            obj.created_by = request.user
        if hasattr(obj, "updated_by_id"):
            obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        record_audit(
            actor=request.user,
            action=f"{self.audit_prefix}.{'update' if change else 'create'}",
            instance=obj,
            source="admin",
            old_data=old_data,
            new_data=_snapshot(obj),
        )


@admin.register(QualificationCategory)
class QualificationCategoryAdmin(PermissionedAuditAdmin):
    permission_codename = "manage_qualification_catalog"
    audit_prefix = "qualification.category"
    list_display = ("name", "slug", "position", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")


@admin.register(QualificationCatalogEntry)
class QualificationCatalogEntryAdmin(PermissionedAuditAdmin):
    permission_codename = "manage_qualification_catalog"
    audit_prefix = "qualification.catalog_entry"
    list_display = ("name", "stable_id", "kind", "category", "position", "is_active")
    list_filter = ("kind", "category", "is_active")
    search_fields = ("name", "stable_id", "description")


@admin.register(RegistrationQualificationMapping)
class RegistrationQualificationMappingAdmin(PermissionedAuditAdmin):
    permission_codename = "manage_qualification_mappings"
    audit_prefix = "qualification.mapping"
    list_display = ("form", "question_key", "mapping_type", "answer_value", "catalog_entry", "is_published", "is_active")
    list_filter = ("form", "mapping_type", "is_published", "is_active")
    search_fields = ("question_key", "answer_value", "catalog_entry__name")


class ParticipantQualificationInline(admin.TabularInline):
    model = ParticipantQualification
    extra = 0
    can_delete = False
    fields = ("catalog_entry", "custom_name", "experience_details", "years_of_experience", "notes", "source", "is_self_reported", "is_active")
    readonly_fields = ("source", "is_self_reported")

    def has_view_permission(self, request, obj=None):
        return _has(request, "manage_participant_qualifications")

    def has_add_permission(self, request, obj=None):
        return _has(request, "manage_participant_qualifications")

    def has_change_permission(self, request, obj=None):
        return _has(request, "manage_participant_qualifications")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ParticipantQualification)
class ParticipantQualificationAdmin(PermissionedAuditAdmin):
    permission_codename = "manage_participant_qualifications"
    audit_prefix = "qualification.participant_record"
    list_display = ("participant", "display_name", "source", "is_self_reported", "is_active", "updated_at")
    list_filter = ("source", "is_self_reported", "is_active")
    search_fields = ("participant__first_name", "participant__last_name", "participant__contact_email", "catalog_entry__name", "custom_name")
    readonly_fields = PermissionedAuditAdmin.readonly_fields + ("source_submission", "source_question_key", "source_answer_hash", "source_answer_snapshot", "mapping_snapshot")

    @admin.display(description="Qualification")
    def display_name(self, obj):
        return obj.catalog_entry.name if obj.catalog_entry_id else obj.custom_name


# Add the qualification section to the canonical participant admin without
# creating another participant profile or changing participant identity.
try:
    from apps.participants.admin import ParticipantAdmin
    ParticipantAdmin.inlines = tuple(getattr(ParticipantAdmin, "inlines", ())) + (ParticipantQualificationInline,)

    _participant_save_formset = ParticipantAdmin.save_formset
    def _audited_participant_save_formset(self, request, form, formset, change):
        if formset.model is not ParticipantQualification:
            return _participant_save_formset(self, request, form, formset, change)
        if not _has(request, "manage_participant_qualifications"):
            raise PermissionError("Not authorized to manage participant qualifications")
        for obj in formset.save(commit=False):
            old_data = _snapshot(ParticipantQualification.objects.get(pk=obj.pk)) if obj.pk else {}
            if not obj.pk:
                obj.created_by = request.user
            obj.updated_by = request.user
            obj.save()
            record_audit(actor=request.user, action=f"qualification.participant_record.{'update' if old_data else 'create'}", instance=obj, source="admin_inline", old_data=old_data, new_data=_snapshot(obj))
        formset.save_m2m()
    ParticipantAdmin.save_formset = _audited_participant_save_formset
except ImportError:
    pass
