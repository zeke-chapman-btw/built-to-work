from django.db import migrations, models


def classify_known_roles(apps, schema_editor):
    Assignment=apps.get_model("staffing","StaffAssignment")
    aliases={"staff":"staff","event staff":"staff","staff lead":"staff_lead",
        "lead":"staff_lead","driver":"driver","combined":"combined","driver + staff":"combined"}
    # Preserve the original descriptive text. Unknown/blank roles require a manager's review.
    for assignment in Assignment.objects.using(schema_editor.connection.alias).filter(coverage_role="").iterator():
        role=aliases.get(assignment.role.strip().casefold())
        if role:
            Assignment.objects.using(schema_editor.connection.alias).filter(pk=assignment.pk,coverage_role="").update(coverage_role=role)


class Migration(migrations.Migration):
    dependencies=[("staffing","0001_initial")]
    operations=[
        migrations.AddField(model_name="staffingplan",name="required_drivers",field=models.PositiveSmallIntegerField(default=0)),
        migrations.AddField(model_name="dailystaffingneed",name="required_drivers",field=models.PositiveSmallIntegerField(default=0)),
        migrations.AddField(model_name="staffassignment",name="coverage_role",field=models.CharField(blank=True,choices=[("staff","Staff"),("staff_lead","Staff Lead"),("driver","Driver"),("combined","Combined (Driver + Staff)")],default="",max_length=16)),
        migrations.RunPython(classify_known_roles,migrations.RunPython.noop),
    ]
