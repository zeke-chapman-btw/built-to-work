from django.db import migrations


def forwards(apps, schema_editor):
    Onboarding = apps.get_model("onboarding", "CustomerOnboarding")
    Document = apps.get_model("onboarding", "CustomerDocument")
    for row in Onboarding.objects.filter(organization__isnull=False):
        if row.w9 and not Document.objects.filter(organization_id=row.organization_id, kind="w9", file=row.w9.name).exists():
            Document.objects.create(organization_id=row.organization_id, kind="w9", title="W-9", file=row.w9.name,
                                    file_name=row.w9_name, onboarding=row)
        if row.signed_at and not Document.objects.filter(onboarding=row, kind="agreement").exists():
            Document.objects.create(organization_id=row.organization_id, kind="agreement", onboarding=row,
                                    title=f"{row.agreement_title or 'Service agreement'} — signed by {row.signed_name}")


class Migration(migrations.Migration):
    dependencies = [("onboarding", "0003_customer_documents")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
