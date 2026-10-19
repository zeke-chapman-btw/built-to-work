from datetime import date
from django.db import migrations

def seed(apps,schema_editor):
    Role=apps.get_model('workdays','StaffRole')
    Rate=apps.get_model('workdays','CompensationRate')
    day=date(2026,10,19)
    reason='Verified authenticated BTW Base44 settings on 2026-10-19; daily basis and role eligibility retained. No historical rates before this date are inferred.'
    for name,amount in [('Staff','200'),('Staff Lead','250'),('Driver','400'),('Combined','600')]:
        role=Role.objects.create(name=name,per_diem_eligible=True)
        Rate.objects.create(kind='role',role=role,amount=amount,effective_from=day,reason=reason)
    Rate.objects.create(kind='per_diem',amount='40',effective_from=day,reason=reason)
    # Base44 fixed mileage tiers are intentionally not converted into a per-mile rate.

class Migration(migrations.Migration):
    dependencies=[('workdays','0001_initial')]
    operations=[migrations.RunPython(seed,migrations.RunPython.noop)]
