from django.db import migrations


def forwards(apps, schema_editor):
    from apps.onboarding.seed import apply
    apply(apps.get_model("onboarding", "OnboardingQuestion"), apps.get_model("onboarding", "AgreementVersion"))


class Migration(migrations.Migration):
    dependencies = [("onboarding", "0001_initial")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
